/** @odoo-module **/

/**
 * EventHub — pub/sub service for real-time CRM Assistant events.
 *
 * Subscribes to the user-specific bus channel and dispatches
 * CRM Assistant notifications to registered callback functions.
 *
 * Components subscribe via:
 *   const eventHub = useService("crm_assistant_event_hub");
 *   eventHub.on("meeting_cancelled", (data) => { ... });
 *
 * Architecture:
 *   Backend _dispatch_event() → bus.bus channel → longpoll → EventHub → callbacks
 */

import { registry } from "@web/core/registry";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";

class EventHub {
    /**
     * @param {Object} env - Odoo environment (contains services)
     */
    constructor(env) {
        this.env = env;
        /** @type {Map<string, Set<Function>>} event → callbacks */
        this._subscribers = new Map();
        /** @type {boolean} whether the bus listener is active */
        this._started = false;
    }

    /**
     * Subscribe to an event type.
     *
     * @param {string} eventType - e.g. "meeting_cancelled", "lead_stage_changed"
     * @param {Function} callback - receives (data) payload
     */
    on(eventType, callback) {
        if (!this._subscribers.has(eventType)) {
            this._subscribers.set(eventType, new Set());
        }
        this._subscribers.get(eventType).add(callback);
    }

    /**
     * Unsubscribe from an event type.
     *
     * @param {string} eventType
     * @param {Function} callback - the exact function passed to on()
     */
    off(eventType, callback) {
        const cbs = this._subscribers.get(eventType);
        if (cbs) {
            cbs.delete(callback);
            if (cbs.size === 0) {
                this._subscribers.delete(eventType);
            }
        }
    }

    /**
     * Start listening to the bus channel.
     * Called once by the first component that needs the hub.
     */
    start() {
        if (this._started) return;
        this._started = true;

        const userId = this._getUserId();
        if (!userId) {
            console.warn("[EventHub] Cannot determine user ID — bus not started");
            return;
        }

        this._channel = `crm_assistant_${userId}`;
        this._listenToBus();
    }

    /**
     * Internal: subscribe to Odoo bus longpolling for our channel.
     */
    _listenToBus() {
        // Use the bus service from the environment if available
        const busService = this.env.services?.bus_service;
        if (busService) {
            busService.addChannel(this._channel);
            busService.addEventListener("notification", this._onBusNotification.bind(this));
        } else {
            // Fallback: use raw browser fetch for longpolling
            console.warn("[EventHub] Bus service not available — using raw longpoll fallback");
            this._pollBusFallback();
        }
    }

    /**
     * Fallback longpoll implementation when bus_service is not available.
     */
    async _pollBusFallback() {
        const { origin } = window.location;
        let last = 0;

        const poll = async () => {
            try {
                const response = await fetch(
                    `${origin}/longpolling/poll`,
                    {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            params: {
                                channels: [this._channel],
                                last: last,
                                bus_inactivity: 300,
                            },
                        }),
                    }
                );
                if (response.ok) {
                    const result = await response.json();
                    if (result.result) {
                        const notifications = result.result;
                        last = notifications[notifications.length - 1]?.id || last;
                        for (const notif of notifications) {
                            if (notif.channel === this._channel) {
                                this._handleNotification(notif.message);
                            }
                        }
                    }
                }
            } catch (_e) {
                // Connection lost — retry after delay
                await new Promise((r) => setTimeout(r, 1000));
            }
            // Continue polling
            this._pollFallbackTimer = setTimeout(poll, 0);
        };

        poll();
    }

    /**
     * Handle a notification from the Odoo bus.
     *
     * @param {Object} event - bus event containing channel + message
     */
    _onBusNotification(event) {
        const notification = event.detail;
        if (Array.isArray(notification)) {
            for (const notif of notification) {
                if (notif.channel === this._channel) {
                    this._handleNotification(notif.message);
                }
            }
        }
    }

    /**
     * Parse and dispatch a notification payload to subscribers.
     *
     * @param {Object} message - { event, data, timestamp }
     */
    _handleNotification(message) {
        if (!message || !message.event) return;

        const { event: eventType, data } = message;
        const cbs = this._subscribers.get(eventType);

        if (cbs && cbs.size > 0) {
            for (const cb of cbs) {
                try {
                    cb(data);
                } catch (err) {
                    console.error(`[EventHub] Error in ${eventType} handler:`, err);
                }
            }
        }
    }

    /**
     * Get the current user ID from the environment.
     *
     * @returns {number|null}
     */
    _getUserId() {
        // Try session info from Odoo's user service
        const userService = this.env.services?.user;
        if (userService?.userId) {
            return userService.userId;
        }
        // Fallback: from window global (set by Odoo backend)
        if (typeof odoo !== "undefined" && odoo.session_info?.uid) {
            return odoo.session_info.uid;
        }
        return null;
    }

    /**
     * Cleanup: remove all subscribers and stop polling.
     */
    destroy() {
        this._subscribers.clear();
        this._started = false;
        if (this._pollFallbackTimer) {
            clearTimeout(this._pollFallbackTimer);
            this._pollFallbackTimer = null;
        }
    }
}

// Register as a service so components can useService("crm_assistant_event_hub")
const eventHubService = {
    dependencies: ["bus_service"],

    /**
     * @param {Object} env
     * @param {Object} services
     */
    start(env, services) {
        const hub = new EventHub(env);
        return hub;
    },
};

registry.category("services").add("crm_assistant_event_hub", eventHubService);
