import { __decorate } from "tslib";
import { LitElement, html, css } from "lit";
import { state } from "lit/decorators.js";
import "./components/ew-text-button";
import "./components/ew-list";
import "./components/ew-list-item";
import "./components/ew-divider";
import "./components/ew-checkbox";
import "./components/ewt-console";
import "./components/ew-dialog";
import "./components/ew-icon-button";
import "./components/ew-filled-text-field";
import "./components/ew-filled-select";
import "./components/ew-select-option";
import "./pages/ewt-page-progress";
import "./pages/ewt-page-message";
import { closeIcon, listItemConsole, listItemEraseUserData, listItemFundDevelopment, listItemHomeAssistant, listItemInstallIcon, listItemVisitDevice, listItemWifi, lockIcon, lockOpenIcon, networkWifi1Bar, networkWifi2Bar, networkWifi3Bar, networkWifiFull, } from "./components/svg";
import { ImprovSerial } from "improv-wifi-serial-sdk/dist/serial";
import { ImprovSerialCurrentState, PortNotReady, } from "improv-wifi-serial-sdk/dist/const";
import { flash } from "./flash";
import { textDownload } from "./util/file-download";
import { fireEvent } from "./util/fire-event";
import { sleep } from "./util/sleep";
import { downloadManifest } from "./util/manifest";
import { dialogStyles } from "./styles";
import { version } from "./version";
console.log(`ESP Web Tools ${version} by Open Home Foundation; https://esphome.github.io/esp-web-tools/`);
const ERROR_ICON = "⚠️";
const OK_ICON = "🎉";
// A device that just booted can come back from its first scan with no networks
// at all. Keep looking (the SDK scans every 3s, so this covers four scans)
// before giving up and showing the form, or we'd tell the user we found nothing
// while the device was still warming up.
const SCAN_GRACE_PERIOD = 9100;
/** Name of the network with the strongest signal, null if there are none. */
const strongestSsid = (ssids) => ssids.length
    ? ssids.reduce((best, ssid) => (ssid.rssi > best.rssi ? ssid : best)).name
    : null;
const signalStrength = (rssi) => {
    if (rssi >= -50)
        return { icon: networkWifiFull, class: "signal-excellent" };
    if (rssi >= -60)
        return { icon: networkWifi3Bar, class: "signal-good" };
    if (rssi >= -70)
        return { icon: networkWifi2Bar, class: "signal-fair" };
    return { icon: networkWifi1Bar, class: "signal-weak" };
};
export class EwtInstallDialog extends LitElement {
    constructor() {
        super(...arguments);
        this.logger = console;
        this._state = "DASHBOARD";
        this._installErase = false;
        this._installConfirmed = false;
        this._provisionForce = false;
        this._wasProvisioned = false;
        this._busy = false;
        // Name of Ssid. Null = other
        this._selectedSsid = null;
        // Prefill for the "Network Name" box shown when "Join other" is selected.
        // Only read while rendering a `_selectedSsid` change, so it needs no @state.
        this._manualSsid = "";
        this._bodyOverflow = null;
        this._handleDisconnect = () => {
            this._state = "ERROR";
            this._error = "Disconnected";
        };
    }
    render() {
        if (!this.port) {
            return html ``;
        }
        let heading;
        let content;
        let allowClosing = false;
        // During installation phase we temporarily remove the client
        if (this._client === undefined &&
            this._state !== "INSTALL" &&
            this._state !== "LOGS") {
            if (this._error) {
                [heading, content] = this._renderError(this._error);
            }
            else {
                content = this._renderProgress("Connecting");
            }
        }
        else if (this._state === "INSTALL") {
            [heading, content, allowClosing] = this._renderInstall();
        }
        else if (this._state === "ASK_ERASE") {
            [heading, content] = this._renderAskErase();
        }
        else if (this._state === "ERROR") {
            [heading, content] = this._renderError(this._error);
        }
        else if (this._state === "DASHBOARD") {
            [heading, content, allowClosing] = this._client
                ? this._renderDashboard()
                : this._renderDashboardNoImprov();
        }
        else if (this._state === "PROVISION") {
            [heading, content] = this._renderProvision();
        }
        else if (this._state === "LOGS") {
            [heading, content] = this._renderLogs();
        }
        return html `
      <ew-dialog
        open
        .heading=${heading}
        @cancel=${this._preventDefault}
        @closed=${this._handleClose}
      >
        ${heading ? html `<div slot="headline">${heading}</div>` : ""}
        ${allowClosing
            ? html `
              <ew-icon-button slot="headline" @click=${this._closeDialog}>
                ${closeIcon}
              </ew-icon-button>
            `
            : ""}
        ${content}
      </ew-dialog>
    `;
    }
    _renderProgress(label, progress) {
        return html `
      <ewt-page-progress
        slot="content"
        .label=${label}
        .progress=${progress}
      ></ewt-page-progress>
    `;
    }
    _renderError(label) {
        const heading = "Error";
        const content = html `
      <ewt-page-message
        slot="content"
        .icon=${ERROR_ICON}
        .label=${label}
      ></ewt-page-message>
      <div slot="actions">
        <ew-text-button @click=${this._closeDialog}>Close</ew-text-button>
      </div>
    `;
        return [heading, content];
    }
    _renderDashboard() {
        const heading = this._manifest.name;
        let content;
        let allowClosing = true;
        content = html `
      <div slot="content">
        <ew-list>
          <ew-list-item>
            <div slot="headline">Connected to ${this._info.name}</div>
            <div slot="supporting-text">
              ${this._info.firmware}&nbsp;${this._info.version}
              (${this._info.chipFamily})
            </div>
          </ew-list-item>
          ${!this._isSameVersion
            ? html `
                <ew-list-item
                  type="button"
                  @click=${() => {
                if (this._isSameFirmware) {
                    this._startInstall(false);
                }
                else if (this._manifest.new_install_prompt_erase) {
                    this._state = "ASK_ERASE";
                }
                else {
                    this._startInstall(true);
                }
            }}
                >
                  ${listItemInstallIcon}
                  <div slot="headline">
                    ${!this._isSameFirmware
                ? `Install ${this._manifest.name}`
                : `Update ${this._manifest.name}`}
                  </div>
                </ew-list-item>
              `
            : ""}
          ${this._client.nextUrl === undefined
            ? ""
            : html `
                <ew-list-item
                  type="link"
                  href=${this._client.nextUrl}
                  target="_blank"
                >
                  ${listItemVisitDevice}
                  <div slot="headline">Visit Device</div>
                </ew-list-item>
              `}
          ${!this._manifest.home_assistant_domain ||
            this._client.state !== ImprovSerialCurrentState.PROVISIONED
            ? ""
            : html `
                <ew-list-item
                  type="link"
                  href=${`https://my.home-assistant.io/redirect/config_flow_start/?domain=${this._manifest.home_assistant_domain}`}
                  target="_blank"
                >
                  ${listItemHomeAssistant}
                  <div slot="headline">Add to Home Assistant</div>
                </ew-list-item>
              `}
          <ew-list-item
            type="button"
            @click=${() => {
            this._state = "PROVISION";
            if (this._client.state === ImprovSerialCurrentState.PROVISIONED) {
                this._provisionForce = true;
            }
        }}
          >
            ${listItemWifi}
            <div slot="headline">
              ${this._client.state === ImprovSerialCurrentState.PROVISIONED
            ? "Change Wi-Fi"
            : "Connect to Wi-Fi"}
            </div>
          </ew-list-item>
          <ew-list-item
            type="button"
            @click=${async () => {
            const client = this._client;
            if (client) {
                await this._closeClientWithoutEvents(client);
                await sleep(100);
            }
            // Also set `null` back to undefined.
            this._client = undefined;
            this._state = "LOGS";
        }}
          >
            ${listItemConsole}
            <div slot="headline">Logs & Console</div>
          </ew-list-item>
          ${this._isSameFirmware && this._manifest.funding_url
            ? html `
                <ew-list-item
                  type="link"
                  href=${this._manifest.funding_url}
                  target="_blank"
                >
                  ${listItemFundDevelopment}
                  <div slot="headline">Fund Development</div>
                </ew-list-item>
              `
            : ""}
          ${this._isSameVersion
            ? html `
                <ew-list-item
                  type="button"
                  class="danger"
                  @click=${() => this._startInstall(true)}
                >
                  ${listItemEraseUserData}
                  <div slot="headline">Erase User Data</div>
                </ew-list-item>
              `
            : ""}
        </ew-list>
      </div>
    `;
        return [heading, content, allowClosing];
    }
    _renderDashboardNoImprov() {
        const heading = this._manifest.name;
        let content;
        let allowClosing = true;
        content = html `
      <div slot="content">
        <ew-list>
          <ew-list-item
            type="button"
            @click=${() => {
            if (this._manifest.new_install_prompt_erase) {
                this._state = "ASK_ERASE";
            }
            else {
                // Default is to erase a device that does not support Improv Serial
                this._startInstall(true);
            }
        }}
          >
            ${listItemInstallIcon}
            <div slot="headline">${`Install ${this._manifest.name}`}</div>
          </ew-list-item>
          <ew-list-item
            type="button"
            @click=${async () => {
            // Also set `null` back to undefined.
            this._client = undefined;
            this._state = "LOGS";
        }}
          >
            ${listItemConsole}
            <div slot="headline">Logs & Console</div>
          </ew-list-item>
        </ew-list>
      </div>
    `;
        return [heading, content, allowClosing];
    }
    _renderProvision() {
        var _a;
        let heading = "Configure Wi-Fi";
        let content;
        if (this._busy) {
            return [heading, this._renderProgress("Trying to connect")];
        }
        if (this._client.state === ImprovSerialCurrentState.STOPPED) {
            heading = undefined;
            content = html `
        <div slot="content">
          <ewt-page-message
            .icon=${ERROR_ICON}
            .label=${html `The connected device has Wi-Fi turned off, so it can't
              be configured right now.<br />Enable the device's Wi-Fi, then try
              again.`}
          ></ewt-page-message>
        </div>
        <div slot="actions">
          <ew-text-button
            @click=${() => {
                this._state = "DASHBOARD";
            }}
          >
            Back
          </ew-text-button>
        </div>
      `;
        }
        else if (!this._provisionForce &&
            this._client.state === ImprovSerialCurrentState.PROVISIONED) {
            heading = undefined;
            const showSetupLinks = !this._wasProvisioned &&
                (this._client.nextUrl !== undefined ||
                    "home_assistant_domain" in this._manifest);
            content = html `
        <div slot="content">
          <ewt-page-message
            .icon=${OK_ICON}
            label="Device connected to the network!"
          ></ewt-page-message>
          ${showSetupLinks
                ? html `
                <ew-list>
                  ${this._client.nextUrl === undefined
                    ? ""
                    : html `
                        <ew-list-item
                          type="link"
                          href=${this._client.nextUrl}
                          target="_blank"
                          @click=${() => {
                        this._state = "DASHBOARD";
                    }}
                        >
                          ${listItemVisitDevice}
                          <div slot="headline">Visit Device</div>
                        </ew-list-item>
                      `}
                  ${!this._manifest.home_assistant_domain
                    ? ""
                    : html `
                        <ew-list-item
                          type="link"
                          href=${`https://my.home-assistant.io/redirect/config_flow_start/?domain=${this._manifest.home_assistant_domain}`}
                          target="_blank"
                          @click=${() => {
                        this._state = "DASHBOARD";
                    }}
                        >
                          ${listItemHomeAssistant}
                          <div slot="headline">Add to Home Assistant</div>
                        </ew-list-item>
                      `}
                  <ew-list-item
                    type="button"
                    @click=${() => {
                    this._state = "DASHBOARD";
                }}
                  >
                    <div slot="start" class="fake-icon"></div>
                    <div slot="headline">Skip</div>
                  </ew-list-item>
                </ew-list>
              `
                : ""}
        </div>

        ${!showSetupLinks
                ? html `
              <div slot="actions">
                <ew-text-button
                  @click=${() => {
                    this._state = "DASHBOARD";
                }}
                >
                  Continue
                </ew-text-button>
              </div>
            `
                : ""}
      `;
        }
        else if (this._ssids === undefined) {
            // Waiting for the first scan to come back.
            content = this._renderProgress("Scanning for networks");
        }
        else {
            let error;
            switch (this._client.error) {
                case 3 /* ImprovSerialErrorState.UNABLE_TO_CONNECT */:
                    error = "Unable to connect";
                    break;
                case 254 /* ImprovSerialErrorState.TIMEOUT */:
                    error = "Timeout";
                    break;
                case 0 /* ImprovSerialErrorState.NO_ERROR */:
                // Happens when list SSIDs not supported.
                case 2 /* ImprovSerialErrorState.UNKNOWN_RPC_COMMAND */:
                    break;
                default:
                    error = `Unknown error (${this._client.error})`;
            }
            const selectedSsid = (_a = this._ssids) === null || _a === void 0 ? void 0 : _a.find((info) => info.name === this._selectedSsid);
            content = html `
        <div slot="content">
          <div>Connect your device to the network to start using it.</div>
          ${error ? html `<p class="error">${error}</p>` : ""}
          ${this._ssids !== null
                ? html `
                <ew-filled-select
                  menu-positioning="fixed"
                  label="Network"
                  @change=${(ev) => {
                    const index = ev.target.selectedIndex;
                    // The "Join Other" item is always the last item.
                    this._selectedSsid =
                        index === this._ssids.length
                            ? null
                            : this._ssids[index].name;
                    // Picking "Join other" ourselves starts from a blank name.
                    this._manualSsid = "";
                }}
                >
                  ${this._ssids.map((info) => {
                    const signal = signalStrength(info.rssi);
                    return html `
                      <ew-select-option
                        .selected=${selectedSsid === info}
                        .value=${info.name}
                      >
                        <span slot="start" class=${signal.class}>
                          ${signal.icon}
                        </span>
                        <span slot="headline">${info.name}</span>
                        <span slot="end" class="network-details">
                          <span class="signal-strength">${info.rssi}dB</span>
                          <span
                            class=${info.secured
                        ? "lock-secured"
                        : "lock-unsecured"}
                          >
                            ${info.secured ? lockIcon : lockOpenIcon}
                          </span>
                        </span>
                      </ew-select-option>
                    `;
                })}
                  <ew-divider></ew-divider>
                  <ew-select-option .selected=${!selectedSsid}>
                    Join other…
                  </ew-select-option>
                </ew-filled-select>
              `
                : ""}
          ${
            // Show input box if command not supported or "Join Other" selected
            !selectedSsid
                ? html `
                  <ew-filled-text-field
                    label="Network Name"
                    name="ssid"
                    .value=${this._manualSsid}
                  ></ew-filled-text-field>
                `
                : ""}
          ${!selectedSsid || selectedSsid.secured
                ? html `
                <ew-filled-text-field
                  label="Password"
                  name="password"
                  type="password"
                  @keydown=${(ev) => {
                    if (ev.key === "Enter") {
                        this._doProvision();
                    }
                }}
                ></ew-filled-text-field>
              `
                : ""}
        </div>
        <div slot="actions">
          <ew-text-button
            @click=${() => {
                this._state = "DASHBOARD";
            }}
          >
            ${this._installState && this._installErase ? "Skip" : "Back"}
          </ew-text-button>
          <ew-text-button @click=${this._doProvision}>Connect</ew-text-button>
        </div>
      `;
        }
        return [heading, content];
    }
    _renderAskErase() {
        const heading = "Erase device";
        const content = html `
      <div slot="content">
        <div>
          Do you want to erase the device before installing
          ${this._manifest.name}? All data on the device will be lost.
        </div>
        <label class="formfield">
          <ew-checkbox touch-target="wrapper" class="danger"></ew-checkbox>
          Erase device
        </label>
      </div>
      <div slot="actions">
        <ew-text-button
          @click=${() => {
            this._state = "DASHBOARD";
        }}
        >
          Back
        </ew-text-button>
        <ew-text-button
          @click=${() => {
            const checkbox = this.shadowRoot.querySelector("ew-checkbox");
            this._startInstall(checkbox.checked);
        }}
        >
          Next
        </ew-text-button>
      </div>
    `;
        return [heading, content];
    }
    _renderInstall() {
        let heading;
        let content;
        const allowClosing = false;
        const isUpdate = !this._installErase && this._isSameFirmware;
        if (!this._installConfirmed && this._isSameVersion) {
            heading = "Erase User Data";
            content = html `
        <div slot="content">
          Do you want to reset your device and erase all user data from your
          device?
        </div>
        <div slot="actions">
          <ew-text-button class="danger" @click=${this._confirmInstall}>
            Erase User Data
          </ew-text-button>
        </div>
      `;
        }
        else if (!this._installConfirmed) {
            heading = "Confirm Installation";
            const action = isUpdate ? "update to" : "install";
            content = html `
        <div slot="content">
          ${isUpdate
                ? html `Your device is running
                ${this._info.firmware}&nbsp;${this._info.version}.<br /><br />`
                : ""}
          Do you want to ${action}
          ${this._manifest.name}&nbsp;${this._manifest.version}?
          ${this._installErase
                ? html `<br /><br />All data on the device will be erased.`
                : ""}
        </div>
        <div slot="actions">
          <ew-text-button
            @click=${() => {
                this._state = "DASHBOARD";
            }}
          >
            Back
          </ew-text-button>
          <ew-text-button @click=${this._confirmInstall}>
            Install
          </ew-text-button>
        </div>
      `;
        }
        else if (!this._installState ||
            this._installState.state === "initializing" /* FlashStateType.INITIALIZING */ ||
            this._installState.state === "preparing" /* FlashStateType.PREPARING */) {
            heading = "Installing";
            content = this._renderProgress("Preparing installation");
        }
        else if (this._installState.state === "erasing" /* FlashStateType.ERASING */) {
            heading = "Installing";
            content = this._renderProgress("Erasing");
        }
        else if (this._installState.state === "writing" /* FlashStateType.WRITING */ ||
            // When we're finished, keep showing this screen with 100% written
            // until Improv is initialized / not detected.
            (this._installState.state === "finished" /* FlashStateType.FINISHED */ &&
                this._client === undefined)) {
            heading = "Installing";
            let percentage;
            let undeterminateLabel;
            if (this._installState.state === "finished" /* FlashStateType.FINISHED */) {
                // We're done writing and detecting improv, show spinner
                undeterminateLabel = "Wrapping up";
            }
            else if (this._installState.details.percentage < 4) {
                // We're writing the firmware under 4%, show spinner or else we don't show any pixels
                undeterminateLabel = "Installing";
            }
            else {
                // We're writing the firmware over 4%, show progress bar
                percentage = this._installState.details.percentage;
            }
            content = this._renderProgress(html `
          ${undeterminateLabel ? html `${undeterminateLabel}<br />` : ""}
          <br />
          This will take
          ${this._installState.chipFamily === "ESP8266"
                ? "a minute"
                : "2 minutes"}.<br />
          Keep this page visible to prevent slow down
        `, percentage);
        }
        else if (this._installState.state === "finished" /* FlashStateType.FINISHED */) {
            heading = undefined;
            const supportsImprov = this._client !== null;
            content = html `
        <ewt-page-message
          slot="content"
          .icon=${OK_ICON}
          label="Installation complete!"
        ></ewt-page-message>

        <div slot="actions">
          <ew-text-button
            @click=${() => {
                this._state =
                    supportsImprov && this._installErase
                        ? "PROVISION"
                        : "DASHBOARD";
            }}
          >
            Next
          </ew-text-button>
        </div>
      `;
        }
        else if (this._installState.state === "error" /* FlashStateType.ERROR */) {
            heading = "Installation failed";
            content = html `
        <ewt-page-message
          slot="content"
          .icon=${ERROR_ICON}
          .label=${this._installState.message}
        ></ewt-page-message>
        <div slot="actions">
          <ew-text-button
            @click=${async () => {
                this._initialize();
                this._state = "DASHBOARD";
            }}
          >
            Back
          </ew-text-button>
        </div>
      `;
        }
        return [heading, content, allowClosing];
    }
    _renderLogs() {
        let heading = `Logs`;
        let content;
        content = html `
      <div slot="content">
        <ewt-console .port=${this.port} .logger=${this.logger}></ewt-console>
      </div>
      <div slot="actions">
        <ew-text-button
          @click=${async () => {
            await this.shadowRoot.querySelector("ewt-console").reset();
        }}
        >
          Reset Device
        </ew-text-button>
        <ew-text-button
          @click=${() => {
            textDownload(this.shadowRoot.querySelector("ewt-console").logs(), `esp-web-tools-logs.txt`);
            this.shadowRoot.querySelector("ewt-console").reset();
        }}
        >
          Download Logs
        </ew-text-button>
        <ew-text-button
          @click=${async () => {
            await this.shadowRoot.querySelector("ewt-console").disconnect();
            this._state = "DASHBOARD";
            this._initialize();
        }}
        >
          Back
        </ew-text-button>
      </div>
    `;
        return [heading, content];
    }
    willUpdate(changedProps) {
        if (!changedProps.has("_state")) {
            return;
        }
        // Clear errors when changing between pages unless we change
        // to the error page.
        if (this._state !== "ERROR") {
            this._error = undefined;
        }
        // Scan for networks from scratch every time we enter provisioning.
        // `_syncScanning` picks it up from here.
        if (this._state === "PROVISION") {
            this._ssids = undefined;
        }
        else {
            // Reset this value if we leave provisioning.
            this._provisionForce = false;
        }
        if (this._state === "INSTALL") {
            this._installConfirmed = false;
            this._installState = undefined;
        }
    }
    /**
     * Return if the provision page shows the network form (and not a message).
     */
    get _showsProvisionForm() {
        var _a;
        const clientState = (_a = this._client) === null || _a === void 0 ? void 0 : _a.state;
        return (clientState !== undefined &&
            clientState !== ImprovSerialCurrentState.STOPPED &&
            (this._provisionForce ||
                clientState !== ImprovSerialCurrentState.PROVISIONED));
    }
    // Scan while (and only while) the network form is shown. Driven from
    // `updated()`, so entering the form starts scanning and leaving it stops.
    _syncScanning() {
        const shouldScan = this._state === "PROVISION" && !this._busy && this._showsProvisionForm;
        if (shouldScan === !!this._unsubSSIDs) {
            return;
        }
        if (!shouldScan) {
            this._stopScanning();
            return;
        }
        // Give a device that comes back empty-handed a little longer before we
        // show the form and tell the user there are no networks.
        this._scanGraceTimeout = setTimeout(() => {
            this._scanGraceTimeout = undefined;
            if (this._ssids === undefined) {
                this._ssids = [];
                this._selectedSsid = null;
            }
        }, SCAN_GRACE_PERIOD);
        // `null` means the device can't scan, and we ask for the network manually.
        this._unsubSSIDs = this._client.subscribeSSIDs((ssids) => {
            // Keep waiting while a device that hasn't found anything yet is still
            // within its grace period.
            if (this._ssids === undefined &&
                (ssids === null || ssids === void 0 ? void 0 : ssids.length) === 0 &&
                this._scanGraceTimeout) {
                return;
            }
            // A scan error after we already have networks is transient, not "device
            // can't scan" (e.g. a late failure packet from a just-failed provision
            // rejecting our scan). Keep the list we have; scanning stops until we
            // re-enter the form.
            if (ssids === null && this._ssids) {
                return;
            }
            if (this._ssids === undefined) {
                // First result. Preselect the strongest network, or "Join other" if
                // the device can't scan or found nothing.
                this._selectedSsid = ssids === null ? null : strongestSsid(ssids);
            }
            else if (this._selectedSsid !== null &&
                !(ssids === null || ssids === void 0 ? void 0 : ssids.some((ssid) => ssid.name === this._selectedSsid))) {
                // The selected network dropped off the list. A subscription merges scans
                // from scratch, so this happens when we resume scanning (ie. after a
                // failed provision) and the first scan misses it. Fall back to "Join
                // other" prefilled with the network they picked, so they keep the data
                // they entered and can simply hit Connect again.
                this._manualSsid = this._selectedSsid;
                this._selectedSsid = null;
            }
            this._ssids = ssids;
        });
    }
    async _stopScanning() {
        clearTimeout(this._scanGraceTimeout);
        this._scanGraceTimeout = undefined;
        const unsubscribe = this._unsubSSIDs;
        if (!unsubscribe) {
            return;
        }
        this._unsubSSIDs = undefined;
        await unsubscribe();
    }
    firstUpdated(changedProps) {
        super.firstUpdated(changedProps);
        this._bodyOverflow = document.body.style.overflow;
        document.body.style.overflow = "hidden";
        this._initialize();
    }
    updated(changedProps) {
        super.updated(changedProps);
        if (changedProps.has("_state")) {
            this.setAttribute("state", this._state);
        }
        this._syncScanning();
        if (this._state !== "PROVISION") {
            return;
        }
        if (changedProps.has("_selectedSsid") && this._selectedSsid === null) {
            // If we pick "Join other", select SSID input.
            this._focusFormElement("ew-filled-text-field[name=ssid]");
        }
        else if (changedProps.has("_ssids") &&
            changedProps.get("_ssids") === undefined) {
            // Form is shown when SSIDs are first loaded/marked not supported. Later
            // scans must not steal focus back from whatever the user is filling in.
            this._focusFormElement();
        }
    }
    _focusFormElement(selector = "ew-filled-text-field, ew-filled-select") {
        const formEl = this.shadowRoot.querySelector(selector);
        if (formEl) {
            formEl.updateComplete.then(() => setTimeout(() => formEl.focus(), 100));
        }
    }
    async _initialize(justInstalled = false) {
        if (this.port.readable === null || this.port.writable === null) {
            this._state = "ERROR";
            this._error =
                "Serial port is not readable/writable. Close any other application using it and try again.";
            return;
        }
        try {
            this._manifest = await downloadManifest(this.manifestPath);
        }
        catch (err) {
            this._state = "ERROR";
            this._error = "Failed to download manifest";
            return;
        }
        if (this._manifest.new_install_improv_wait_time === 0) {
            this._client = null;
            return;
        }
        const client = new ImprovSerial(this.port, this.logger);
        client.addEventListener("state-changed", () => {
            this.requestUpdate();
        });
        client.addEventListener("error-changed", () => this.requestUpdate());
        try {
            // Improv re-sends the request every second until this timeout, so 1500ms
            // gets us a single retry. That gives a device that was busy on our first
            // try (ie. still streaming the results of a Wi-Fi scan from before a page
            // reload) a second chance to answer us.
            // If a device was just installed, give new firmware 10 seconds (overridable) to
            // format the rest of the flash and do other stuff.
            const timeout = !justInstalled
                ? 1500
                : this._manifest.new_install_improv_wait_time !== undefined
                    ? this._manifest.new_install_improv_wait_time * 1000
                    : 10000;
            this._info = await client.initialize(timeout);
            this._client = client;
            client.addEventListener("disconnect", this._handleDisconnect);
        }
        catch (err) {
            // Clear old value
            this._info = undefined;
            if (err instanceof PortNotReady) {
                this._state = "ERROR";
                this._error =
                    "Serial port is not ready. Close any other application using it and try again.";
            }
            else {
                this._client = null; // not supported
                this.logger.error("Improv initialization failed.", err);
            }
        }
    }
    _startInstall(erase) {
        this._state = "INSTALL";
        this._installErase = erase;
        this._installConfirmed = false;
    }
    async _confirmInstall() {
        this._installConfirmed = true;
        this._installState = undefined;
        if (this._client) {
            await this._closeClientWithoutEvents(this._client);
        }
        this._client = undefined;
        // Close port. ESPLoader likes opening it.
        await this.port.close();
        flash((state) => {
            this._installState = state;
            if (state.state === "finished" /* FlashStateType.FINISHED */) {
                sleep(100)
                    // Flashing closes the port
                    .then(() => this.port.open({ baudRate: 115200, bufferSize: 8192 }))
                    .then(() => this._initialize(true))
                    .then(() => this.requestUpdate());
            }
            else if (state.state === "error" /* FlashStateType.ERROR */) {
                sleep(100)
                    // Flashing closes the port
                    .then(() => this.port.open({ baudRate: 115200, bufferSize: 8192 }));
            }
        }, this.port, this.manifestPath, this._manifest, this._installErase);
    }
    async _doProvision() {
        var _a;
        // Read the form before setting `_busy`: that swaps the form for a progress
        // view, and the render happens during any await below — after which these
        // fields no longer exist and we'd provision with an empty password.
        const ssid = this._selectedSsid === null
            ? this.shadowRoot.querySelector("ew-filled-text-field[name=ssid]").value
            : this._selectedSsid;
        const password = ((_a = this.shadowRoot.querySelector("ew-filled-text-field[name=password]")) === null || _a === void 0 ? void 0 : _a.value) || "";
        this._busy = true;
        this._wasProvisioned =
            this._client.state === ImprovSerialCurrentState.PROVISIONED;
        // Wait for any in-flight scan to settle before provisioning so we don't
        // have two RPC commands in flight at once. Marking busy above already tells
        // `_syncScanning` to stop, but the provision RPC needs it stopped *now*.
        await this._stopScanning();
        try {
            // Devices try to connect for ~30s before reporting failure. Our timeout
            // must comfortably exceed that: it's only a safety net, and if it fires
            // first the device's late failure packet rejects the *next* RPC we send
            // (the resumed network scan) instead of this one.
            await this._client.provision(ssid, password, 45000);
        }
        catch (err) {
            return;
        }
        finally {
            // If we end up back on the network form, `_syncScanning` resumes scanning.
            this._busy = false;
            this._provisionForce = false;
        }
    }
    _closeDialog() {
        this.shadowRoot.querySelector("ew-dialog").close();
    }
    async _handleClose() {
        if (this._client) {
            await this._closeClientWithoutEvents(this._client);
        }
        fireEvent(this, "closed");
        document.body.style.overflow = this._bodyOverflow;
        this.parentNode.removeChild(this);
    }
    /**
     * Return if the device runs same firmware as manifest.
     */
    get _isSameFirmware() {
        var _a;
        return !this._info
            ? false
            : ((_a = this.overrides) === null || _a === void 0 ? void 0 : _a.checkSameFirmware)
                ? this.overrides.checkSameFirmware(this._manifest, this._info)
                : this._info.firmware === this._manifest.name;
    }
    /**
     * Return if the device runs same firmware and version as manifest.
     */
    get _isSameVersion() {
        return (this._isSameFirmware && this._info.version === this._manifest.version);
    }
    async _closeClientWithoutEvents(client) {
        // Let an in-flight scan settle before we pull the port out from under it.
        await this._stopScanning();
        client.removeEventListener("disconnect", this._handleDisconnect);
        await client.close();
    }
    _preventDefault(ev) {
        ev.preventDefault();
    }
}
EwtInstallDialog.styles = [
    dialogStyles,
    css `
      :host {
        --mdc-dialog-max-width: 390px;
      }
      div[slot="headline"] {
        padding-right: 48px;
      }
      ew-icon-button[slot="headline"] {
        position: absolute;
        right: 4px;
        top: 8px;
      }
      ew-icon-button[slot="headline"] svg {
        padding: 8px;
        color: var(--text-color);
      }
      .dialog-nav svg {
        color: var(--text-color);
      }
      .table-row {
        display: flex;
      }
      .table-row.last {
        margin-bottom: 16px;
      }
      .table-row svg {
        width: 20px;
        margin-right: 8px;
      }
      ew-filled-text-field,
      ew-filled-select {
        display: block;
        margin-top: 16px;
      }
      ew-select-option svg {
        width: 24px;
        height: 24px;
        display: block;
      }
      .network-details {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-left: 16px;
        color: var(--text-color);
        font-size: 12px;
      }
      .signal-excellent,
      .signal-good,
      .lock-secured {
        color: #34a853;
      }
      .signal-fair {
        color: #fbbc04;
      }
      .signal-weak,
      .lock-unsecured {
        color: var(--danger-color);
      }
      label.formfield {
        display: inline-flex;
        align-items: center;
        padding-right: 8px;
      }
      ew-list {
        margin: 0 -24px;
        padding: 0;
      }
      ew-list-item svg {
        height: 24px;
      }
      ewt-page-message + ew-list {
        padding-top: 16px;
      }
      .fake-icon {
        width: 24px;
      }
      .error {
        color: var(--danger-color);
      }
      .danger {
        --mdc-theme-primary: var(--danger-color);
        --mdc-theme-secondary: var(--danger-color);
        --md-sys-color-primary: var(--danger-color);
        --md-sys-color-on-surface: var(--danger-color);
      }
      button.link {
        background: none;
        color: inherit;
        border: none;
        padding: 0;
        font: inherit;
        text-align: left;
        text-decoration: underline;
        cursor: pointer;
      }
      :host([state="LOGS"]) ew-dialog {
        max-width: 90vw;
        max-height: 90vh;
      }
      ewt-console {
        width: calc(80vw - 48px);
        height: calc(90vh - 168px);
      }
    `,
];
__decorate([
    state()
], EwtInstallDialog.prototype, "_client", void 0);
__decorate([
    state()
], EwtInstallDialog.prototype, "_state", void 0);
__decorate([
    state()
], EwtInstallDialog.prototype, "_installErase", void 0);
__decorate([
    state()
], EwtInstallDialog.prototype, "_installConfirmed", void 0);
__decorate([
    state()
], EwtInstallDialog.prototype, "_installState", void 0);
__decorate([
    state()
], EwtInstallDialog.prototype, "_provisionForce", void 0);
__decorate([
    state()
], EwtInstallDialog.prototype, "_error", void 0);
__decorate([
    state()
], EwtInstallDialog.prototype, "_busy", void 0);
__decorate([
    state()
], EwtInstallDialog.prototype, "_ssids", void 0);
__decorate([
    state()
], EwtInstallDialog.prototype, "_selectedSsid", void 0);
customElements.define("ewt-install-dialog", EwtInstallDialog);
