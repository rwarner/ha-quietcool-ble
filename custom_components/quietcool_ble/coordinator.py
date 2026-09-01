"""QuietCool BLE coordinator — owns all BLE connections for one fan device.

Architecture: led_ble dual-lock pattern.
  _connect_lock   — serializes establish_connection calls
  _operation_lock — serializes GATT writes within an open connection

All BLE operations (poll AND entity commands) go through async_execute().
Fan entities NEVER open their own BleakClient connections.

Critical: _handle_disconnect does NOT call asyncio.all_tasks().cancel().
The upstream emerose/quietcool library does this; porting that line would
crash the entire HA event loop on every BLE disconnect.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias

from bleak import BleakClient
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry
from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from . import api
from .api import FanInfo, FanParameters, FanState, FanVersion
from .const import (
    KEEP_ALIVE_SECONDS,
    MAX_CONNECT_ATTEMPTS,
    POLL_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

_BleOperation: TypeAlias = Callable[[BleakClient], Awaitable[None]]

# Consecutive login rejections before we conclude the PhoneID was evicted and
# prompt the user to re-pair. Guards against transient/garbled BLE reads.
AUTH_FAILURE_LIMIT = 3


class QuietCoolBLECoordinator(ActiveBluetoothDataUpdateCoordinator[None]):
    """Manages all BLE communication for a single QuietCool fan device."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        entry: ConfigEntry,
        address: str,
        phone_id: str,
        fan_info: FanInfo,
    ) -> None:
        super().__init__(
            hass,
            logger,
            address=address,
            mode=BluetoothScanningMode.ACTIVE,
            needs_poll_method=self._needs_poll,
            poll_method=self._async_poll,
            connectable=True,
        )
        self._entry = entry
        self.phone_id = phone_id
        self.fan_info: FanInfo = fan_info
        self.fan_state: FanState | None = None
        self.fan_version: FanVersion | None = None
        self.fan_parameters: FanParameters | None = None

        logger.info(
            "QuietCool coordinator init: %s model=%r serial=%r protocol=%s",
            address,
            fan_info.model,
            fan_info.serial,
            fan_info.protocol,
        )

        # Dual-lock pattern (led_ble reference architecture)
        self._connect_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._client: BleakClientWithServiceCache | None = None
        self._expected_disconnect = False
        self._idle_timer_handle: asyncio.TimerHandle | None = None
        self._poll_timer_handle: asyncio.TimerHandle | None = None
        self._poll_task: asyncio.Task[Any] | None = None
        self._consecutive_failures = 0
        self._auth_failures = 0
        self._reauth_in_progress = False
        self._closing = False

    # ------------------------------------------------------------------
    # Poll scheduling
    # ------------------------------------------------------------------

    @callback
    def _needs_poll(
        self,
        service_info: BluetoothServiceInfoBleak,
        seconds_since_last_poll: float | None,
    ) -> bool:
        """Return True when it's time to connect and read device state."""
        # Allow polls during startup; only block during shutdown to avoid
        # connecting to a device while HA is tearing down.
        ha_ok = self.hass.state not in (CoreState.stopping, CoreState.stopped)
        interval_ok = (
            seconds_since_last_poll is None
            or seconds_since_last_poll > self._poll_interval()
        )
        connectable = bool(
            bluetooth.async_ble_device_from_address(
                self.hass, service_info.device.address, connectable=True
            )
        )
        # Don't poll while a re-pair (reauth) flow is already awaiting the user.
        # We'd just reconnect, fail login, and re-trigger the same flow every
        # interval — needless BLE churn and log spam. If the user dismisses the
        # flow, this goes empty and polling resumes (re-detecting the eviction).
        reauth_pending = any(
            self._entry.async_get_active_flows(self.hass, {SOURCE_REAUTH})
        )
        if not reauth_pending:
            # No reauth flow outstanding — clear the synchronous guard so a later
            # eviction can prompt again (e.g. after the user dismissed the prompt).
            self._reauth_in_progress = False
        result = ha_ok and interval_ok and connectable and not reauth_pending
        _LOGGER.debug(
            "QuietCool %s _needs_poll: ha_ok=%s interval_ok=%s "
            "(since_last=%.0fs interval=%.0fs) connectable=%s reauth_pending=%s → %s",
            self.address,
            ha_ok,
            interval_ok,
            seconds_since_last_poll if seconds_since_last_poll is not None else -1,
            self._poll_interval(),
            connectable,
            reauth_pending,
            result,
        )
        return result

    def _poll_interval(self) -> float:
        """Return poll interval with exponential backoff after consecutive failures."""
        if self._consecutive_failures == 0:
            return POLL_INTERVAL_SECONDS
        backoff = POLL_INTERVAL_SECONDS * (2**self._consecutive_failures)
        return min(backoff, 300.0)  # cap at 5 minutes

    async def async_request_refresh(self) -> None:
        """Poll on demand (homeassistant.update_entity / CoordinatorEntity).

        ActiveBluetoothDataUpdateCoordinator has no async_request_refresh(), but
        every entity here is a CoordinatorEntity whose async_update() calls it —
        so `homeassistant.update_entity` raised AttributeError (issue #10).

        Calls _async_poll() directly rather than the base's private debouncer:
        depending on private base attrs is what caused the original AttributeError,
        and the debouncer is the component that wedges — going through it would
        inherit the freeze instead of giving users a manual unstick lever.
        """
        if self._poll_task is not None and not self._poll_task.done():
            _LOGGER.debug(
                "QuietCool %s: refresh requested, poll already in flight",
                self.address,
            )
            return
        await self._async_poll()

    async def _async_poll(self, service_info: BluetoothServiceInfoBleak | None = None) -> None:
        """Perform one poll cycle: connect, login, GetWorkState, disconnect (or keep-alive)."""
        _LOGGER.debug("QuietCool %s: _async_poll invoked", self.address)
        self._poll_task = asyncio.current_task()
        try:
            await self.async_execute(self._poll_operation)
            if self._consecutive_failures > 0:
                _LOGGER.info(
                    "QuietCool %s: poll recovered after %d failure(s)",
                    self.address,
                    self._consecutive_failures,
                )
            self._consecutive_failures = 0
            # Notify entity listeners directly. ActiveBluetoothDataUpdateCoordinator
            # only fires _listeners on BLE advertisement events, not after polls.
            for update_callback, _ in list(self._listeners.values()):
                update_callback()
        except ConfigEntryAuthFailed:
            # Reauth was already started in _ensure_connected. Swallow without
            # rescheduling: a successful re-pair reloads the entry, and _needs_poll
            # suppresses polls while the reauth flow is pending.
            _LOGGER.debug(
                "QuietCool %s: poll skipped — awaiting re-pair", self.address
            )
        except UpdateFailed:
            self._consecutive_failures += 1
            _LOGGER.debug(
                "QuietCool %s: poll failure #%d, next interval %.0fs",
                self.address,
                self._consecutive_failures,
                self._poll_interval(),
            )
            self._schedule_poll_timer()
        except Exception as err:  # noqa: BLE001
            # Unexpected exception — not wrapped by async_execute (e.g. an error
            # in the listener callback loop). Still schedule a retry so polling
            # never stops permanently. asyncio.CancelledError is BaseException and
            # won't be caught here, which is correct — no retry on task cancellation.
            self._consecutive_failures += 1
            _LOGGER.exception(
                "QuietCool %s: unexpected poll error (failure #%d): %s",
                self.address,
                self._consecutive_failures,
                err,
            )
            self._schedule_poll_timer()
        finally:
            self._poll_task = None

    async def _poll_operation(self, client: BleakClient) -> None:
        # Fetch firmware version once — it never changes during a device's lifetime
        if self.fan_version is None:
            try:
                self.fan_version = await api.get_version_info(
                    client,
                    protocol=self.fan_info.protocol,
                )
                _LOGGER.info(
                    "QuietCool %s firmware=%s hw=%s protect_temp=%d°F",
                    self.address,
                    self.fan_version.firmware,
                    self.fan_version.hw_version,
                    self.fan_version.protect_temp,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("QuietCool %s GetVersion failed: %s", self.address, err)

        # Fetch parameters every poll — user may change them from the app
        try:
            self.fan_parameters = await api.get_parameters(
                client,
                protocol=self.fan_info.protocol,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("QuietCool %s GetParameter failed: %s", self.address, err)

        # Work state — get this before RemainTime so we know the current mode
        state = await api.get_work_state(client, protocol=self.fan_info.protocol)

        # Only fetch timer countdown in Timer mode. The device returns its last
        # stored timer duration (not a live countdown) in TH and Idle modes, which
        # would show a misleading non-zero value in the Timer Remaining sensor.
        remain_seconds = 0
        if state.mode == api.FanMode.TIMER:
            try:
                remain = await api.get_remain_time(
                    client,
                    protocol=self.fan_info.protocol,
                )
                remain_seconds = (
                    int(remain.get("RemainHour", 0)) * 3600
                    + int(remain.get("RemainMinute", 0)) * 60
                    + int(remain.get("RemainSecond", 0))
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("QuietCool %s GetRemainTime failed: %s", self.address, err)

        self.fan_state = dataclasses.replace(state, remain_seconds=remain_seconds)

        _LOGGER.debug(
            "QuietCool %s: state mode=%s range=%s temp=%s hum=%s remain=%ds listeners=%d",
            self.address,
            self.fan_state.mode,
            self.fan_state.range,
            self.fan_state.temp_fahrenheit,
            self.fan_state.humidity_percent,
            remain_seconds,
            len(self._listeners),
        )

    # ------------------------------------------------------------------
    # Command routing — single entry point for ALL BLE operations
    # ------------------------------------------------------------------

    async def async_execute(self, operation: _BleOperation) -> None:
        """Execute a BLE operation under the connection and operation locks.

        This is the ONLY place that calls _ensure_connected. Fan entities
        must route all commands here; they must never open BleakClient
        connections directly.
        """
        try:
            client = await self._ensure_connected()
            async with self._operation_lock:
                await operation(client)
        except ConfigEntryAuthFailed:
            # _ensure_connected already tore the connection down. Must NOT be
            # masked as UpdateFailed — the poll loop needs it for the reauth flow.
            raise
        except UpdateFailed:
            await self._async_drop_client()
            raise
        except TimeoutError as err:
            await self._async_drop_client()
            raise UpdateFailed(str(err)) from err
        except Exception as err:
            await self._async_drop_client()
            raise UpdateFailed(f"BLE operation failed: {err}") from err

    async def async_apply_smart_params(self, **overrides: Any) -> None:
        """Write TH smart-mode params (SetTempHumidity) and re-assert TH atomically.

        Only the field(s) in ``overrides`` change; the rest are taken from the
        current fan_parameters so unchanged values are preserved (SetTempHumidity
        rewrites all six). When the fan is already in TH mode, SetMode:TH is
        re-sent in the SAME operation so the device re-runs its smart-mode
        decision against the new values immediately — bundled into one
        async_execute so both writes share a single connection and lock hold
        rather than racing an idle disconnect between two separate operations.

        Shared by the threshold number entities and the humidity-speed select so
        the write-then-reassert logic lives in exactly one place. No-op when
        parameters have not been fetched yet.
        """
        params = self.fan_parameters
        if params is None:
            return
        merged = {
            "temp_h": params.temp_h,
            "temp_m": params.temp_m,
            "temp_l": params.temp_l,
            "hum_h": params.hum_h,
            "hum_l": params.hum_l,
            "hum_range": params.hum_range,
        }
        merged.update(overrides)
        protocol = self.fan_info.protocol
        reassert_th = self.fan_state is not None and self.fan_state.mode == api.FanMode.TH

        async def _op(client: BleakClient) -> None:
            await api.set_temp_humidity(client, protocol=protocol, **merged)
            if reassert_th:
                await api.set_mode_th(client, protocol=protocol)

        await self.async_execute(_op)
        self.fan_parameters = dataclasses.replace(params, **overrides)

    async def _async_drop_client(self) -> None:
        """Tear down the connection after a failed BLE operation.

        is_connected is a *cached* property (BlueZ D-Bus property cache / ESPHome
        _is_connected), so on a wedged transport it stays True forever and
        _handle_disconnect never fires. _ensure_connected's lock-free fast path
        would then hand the same dead client to every future poll, for ever
        (issue #10). Dropping it forces the next poll onto the slow path, which
        builds a fresh connection.

        Drops on *any* failure: sniffing "dead transport" vs "transient" by
        exception type is fragile and would leave the hole open. The cost is one
        extra reconnect per real failure, which is already routine (the fan drops
        idle links every ~25s).
        """
        async with self._connect_lock:
            client = self._client
            self._client = None
            if client is not None:
                self._expected_disconnect = True
            # No connection left to idle out. A stale idle timer would otherwise
            # fire later and re-arm the poll timer, pushing the pending backoff
            # poll out by up to one interval. (_handle_disconnect does the same.)
            if self._idle_timer_handle:
                self._idle_timer_handle.cancel()
                self._idle_timer_handle = None
        if client is not None:
            await api.safe_disconnect(client, self.address)

    # ------------------------------------------------------------------
    # Connection lifecycle — led_ble dual-lock pattern
    # ------------------------------------------------------------------

    @callback
    def _start_reauth(self) -> None:
        """Start HA's reauth flow so the user can re-pair. Idempotent and safe.

        Invoked from both the poll loop and the command path when the controller
        rejects our PhoneID. No-op if a reauth flow is already pending, and it
        never lets flow bookkeeping propagate — a failure here must not kill the
        poll loop or a service call.

        `_reauth_in_progress` is set synchronously to bridge the gap between
        async_start_reauth() scheduling the flow and it appearing in
        async_get_active_flows(); without it, a second trigger inside that window
        could start a duplicate flow. _needs_poll clears the flag once no reauth
        flow is outstanding.
        """
        if self._reauth_in_progress or any(
            self._entry.async_get_active_flows(self.hass, {SOURCE_REAUTH})
        ):
            return
        self._reauth_in_progress = True
        _LOGGER.warning(
            "QuietCool %s: authentication rejected — the fan is no longer accepting "
            "our Phone ID. Prompting re-pair.",
            self.address,
        )
        try:
            self._entry.async_start_reauth(self.hass)
        except Exception:  # noqa: BLE001 — reauth bookkeeping must never kill polling
            _LOGGER.exception(
                "QuietCool %s: failed to start reauth flow", self.address
            )

    async def _ensure_connected(self) -> BleakClientWithServiceCache:
        """Return the active BLE connection, opening one if needed.

        Uses double-check locking: fast path if already connected,
        slow path (acquire lock, reconnect) if disconnected.
        """
        # Fast path: already connected — reset idle timer and return
        if self._client is not None and self._client.is_connected:
            self._reset_idle_timer()
            return self._client

        async with self._connect_lock:
            # Double-check after acquiring lock
            if self._client is not None and self._client.is_connected:
                self._reset_idle_timer()
                return self._client

            device = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if device is None:
                raise UpdateFailed(
                    f"No connectable BLE adapter can reach {self.address}"
                )

            self._expected_disconnect = False
            _LOGGER.debug("QuietCool %s: opening BLE connection", self.address)
            try:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    self.address,
                    disconnected_callback=self._handle_disconnect,
                    max_attempts=MAX_CONNECT_ATTEMPTS,
                )
            except Exception as err:
                raise UpdateFailed(
                    f"Could not connect to QuietCool {self.address}: {err}"
                ) from err

            _LOGGER.debug("QuietCool %s: BLE link up, authenticating", self.address)
            # Authenticate immediately after connecting
            try:
                login_result = await api.login_with_protocol(client, self.phone_id)
                authenticated = login_result.authenticated
            except Exception as err:
                self._expected_disconnect = True
                await api.safe_disconnect(client, self.address)
                raise UpdateFailed(f"BLE login error: {err}") from err

            _LOGGER.debug(
                "QuietCool %s: login result=%s", self.address, authenticated
            )
            if not authenticated:
                self._expected_disconnect = True
                await api.safe_disconnect(client, self.address)
                self._auth_failures += 1
                # A single login=False can be a transient/garbled BLE read, so we
                # retry (UpdateFailed → backoff) a few times before concluding the
                # PhoneID was really evicted. Only after AUTH_FAILURE_LIMIT
                # consecutive failures do we surface the re-pair prompt — otherwise
                # flaky BLE would pop spurious "Reconfigure" cards.
                if self._auth_failures < AUTH_FAILURE_LIMIT:
                    raise UpdateFailed(
                        f"QuietCool login rejected (attempt {self._auth_failures}"
                        f"/{AUTH_FAILURE_LIMIT}); retrying"
                    )
                # Persistent rejection — the fan is no longer accepting our Phone
                # ID (the pairing didn't persist, or the controller dropped it).
                # Start the reauth flow (fires for both poll and command paths) and
                # raise ConfigEntryAuthFailed instead of spinning.
                self._start_reauth()
                raise ConfigEntryAuthFailed(
                    "QuietCool login rejected — the fan is no longer accepting "
                    "Home Assistant's Phone ID. Re-pair, or re-add with a known "
                    "Phone ID, to reconnect."
                )

            self._auth_failures = 0
            if login_result.protocol != self.fan_info.protocol:
                _LOGGER.info(
                    "QuietCool %s: protocol changed from %s to %s",
                    self.address,
                    self.fan_info.protocol,
                    login_result.protocol,
                )
                self.fan_info = dataclasses.replace(
                    self.fan_info,
                    protocol=login_result.protocol,
                )
            self._client = client
            self._reset_idle_timer()
            _LOGGER.debug("QuietCool %s: authenticated, ready to poll", self.address)
            return client

    def _handle_disconnect(self, client: BleakClient) -> None:
        """Handle device-initiated BLE disconnect.

        The QuietCool controller drops the connection after ~25 seconds of
        inactivity. This is normal — we schedule a re-poll so entities stay
        fresh without needing a new BLE advertisement (BT proxies deduplicate).

        IMPORTANT: Does NOT cancel asyncio.all_tasks(). The upstream
        emerose/quietcool library does this, which would crash HA. We cancel
        only our own _poll_task.

        Safe to mutate _client without the lock: this is a sync callback and runs
        atomically on the event loop between awaits.
        """
        # Only honour callbacks from the client we currently hold. Teardown now
        # disconnects outside _connect_lock, so a *new* client can already exist
        # when an old client's callback fires late — without this check it would
        # null the live connection and cancel the live poll task.
        if self._client is not None and client is not self._client:
            _LOGGER.debug(
                "QuietCool %s: ignoring disconnect from a stale BLE client",
                self.address,
            )
            return
        if self._expected_disconnect:
            return
        if self._client is None:
            return  # already handled (Bleak sometimes fires this callback twice)
        _LOGGER.debug(
            "QuietCool %s: BLE connection closed by device (idle timeout or link loss)",
            self.address,
        )
        self._client = None
        if self._idle_timer_handle:
            self._idle_timer_handle.cancel()
            self._idle_timer_handle = None
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
        # Close the old client's D-Bus bus to prevent dbus-daemon connection leak.
        # Bleak's _cleanup_all() fires on unsolicited BLE disconnects but does not
        # close the per-client D-Bus MessageBus, leaking one connection per cycle.
        self.hass.async_create_task(self._async_close_stale_client(client))
        # Schedule next poll — device returns to advertising mode after disconnect
        self._schedule_poll_timer()

    async def _async_close_stale_client(self, client: BleakClient) -> None:
        """Close a stale BleakClient's D-Bus bus after device-initiated disconnect.

        Bleak's _cleanup_all() fires on unsolicited BLE disconnects but does not
        close the per-client D-Bus MessageBus, leaking one dbus-daemon connection
        per BLE reconnect cycle.  Calling disconnect() on an already-disconnected
        client is safe — Bleak skips the BLE teardown and goes straight to
        bus.disconnect(). safe_disconnect bounds it and force-closes the bus if
        disconnect() times out or raises.
        """
        await api.safe_disconnect(client, self.address)

    def _schedule_poll_timer(self) -> None:
        """Schedule a timer-driven poll for after device disconnects.

        BT proxies deduplicate advertisements, so we cannot rely on a BLE
        advertisement event to trigger _needs_poll after the first startup poll.
        Instead, we self-schedule: poll → device idles and disconnects → timer
        fires → reconnect → poll → repeat.
        """
        if self._closing:
            return
        if self._poll_timer_handle is not None:
            self._poll_timer_handle.cancel()
        interval = self._poll_interval()
        _LOGGER.debug(
            "QuietCool %s: scheduling next poll in %.0fs", self.address, interval
        )
        self._poll_timer_handle = self.hass.loop.call_later(
            interval,
            lambda: self.hass.async_create_task(self._async_poll()),
        )

    def _reset_idle_timer(self) -> None:
        """Reset the idle-disconnect timer to KEEP_ALIVE_SECONDS from now."""
        if self._closing:
            return
        if self._idle_timer_handle:
            self._idle_timer_handle.cancel()
        self._idle_timer_handle = self.hass.loop.call_later(
            KEEP_ALIVE_SECONDS, self._schedule_idle_disconnect
        )

    def _schedule_idle_disconnect(self) -> None:
        self.hass.async_create_task(self._async_idle_disconnect())

    async def _async_idle_disconnect(self) -> None:
        """Voluntarily close an idle BLE connection to free the adapter slot.

        Detaches the client *under* _connect_lock, then disconnects *outside* it.
        A wedged disconnect must never hold _connect_lock: that blocked every
        future connect AND hung async_stop(), so unload/reload could never
        complete and only an HA restart recovered (issue #10).

        Teardown is unconditional — no `is_connected` guard. The client is always
        cleared and a poll timer is always armed, because a client that reports
        not-connected still has to be dropped and polling still has to resume.
        (Same bug class as v0.2.2 / v0.2.4, which only fixed narrower paths.)
        """
        # Non-blocking check: don't yank the connection out from under an in-flight
        # GATT op. Never *await* _operation_lock here — that would re-introduce the
        # very wedge this function is being fixed for.
        if self._operation_lock.locked() and not self._closing:
            _LOGGER.debug(
                "QuietCool %s: BLE op in flight, deferring idle disconnect",
                self.address,
            )
            self._reset_idle_timer()
            return

        async with self._connect_lock:
            client = self._client
            self._client = None
            if client is not None:
                self._expected_disconnect = True

        if client is not None:
            await api.safe_disconnect(client, self.address)
            _LOGGER.debug(
                "QuietCool %s: idle disconnect (%.0fs timeout)",
                self.address,
                KEEP_ALIVE_SECONDS,
            )
        self._schedule_poll_timer()

    async def async_stop(self) -> None:
        """Cancel in-flight poll and close any open BLE connection.

        The base ActiveBluetoothDataUpdateCoordinator has no async_stop(); its
        teardown is the unregister callback returned by async_start(), which
        __init__.py registers via entry.async_on_unload(). So we only clean up
        our own poll task, timers, and BLE connection here — calling
        super().async_stop() raised AttributeError and broke every unload/reload
        (and the reauth flow's reload). See issue #8.

        _closing is set FIRST so nothing can re-arm a timer behind us: previously
        we cancelled _poll_timer_handle and then _async_idle_disconnect re-armed
        it, leaking a timer that fired _async_poll() on a dead coordinator and
        raced the new one on reload.
        """
        self._closing = True
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._idle_timer_handle:
            self._idle_timer_handle.cancel()
        if self._poll_timer_handle is not None:
            self._poll_timer_handle.cancel()
            self._poll_timer_handle = None
        await self._async_idle_disconnect()
