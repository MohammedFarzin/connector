odoo.define("connector.services.event_hub", function (require) {
    "use strict";

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

    const { registry } = require("@web/core/registry");
    const { browser } = require("@web/core/browser/browser");
    const { _t } = require("@web/core/l10n/translation");

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
                // Use subscribe() instead of addEventListener() because
                // "notification" is in Odoo's INTERNAL_EVENTS exclusion set
                // and will never fire via addEventListener.
                // Store the callback reference for later unsubscribe().
                this._busCallback = this._onRecordChangedNotification.bind(this);
                busService.subscribe(
                    "crm_assistant_record_changed",
                    this._busCallback
                );
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
                                    // Wrap raw payload with event field for _handleNotification
                                    this._handleNotification({
                                        event: "record_changed",
                                        data: notif.message,
                                        timestamp: new Date().toISOString(),
                                    });
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
         * Handle a crm_assistant_record_changed notification from the bus.
         *
         * Called by busService.subscribe() when the backend dispatches
         * a bus notification on the crm_assistant_{userId} channel.
         *
         * @param {Object} payload - { model, record_ids, method }
         * @param {Object} meta - { id: notificationId }
         */
        _onRecordChangedNotification(payload, meta) {
            if (!payload || !payload.model) return;

            console.debug(
                "[EventHub] Received record change notification:",
                payload.model,
                payload.method,
                payload.record_ids
            );

            // Dispatch to subscribers registered via eventHub.on()
            this._handleNotification({
                event: "record_changed",
                data: payload,
                timestamp: new Date().toISOString(),
            });
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
         * Cleanup: remove all subscribers, stop polling, and
         * unsubscribe from the bus service to prevent memory leaks.
         */
        destroy() {
            this._subscribers.clear();
            this._started = false;
            if (this._pollFallbackTimer) {
                clearTimeout(this._pollFallbackTimer);
                this._pollFallbackTimer = null;
            }
            // Clean up bus subscription to prevent zombie callbacks
            if (this._busCallback) {
                const busService = this.env.services?.bus_service;
                if (busService) {
                    busService.unsubscribe(
                        "crm_assistant_record_changed",
                        this._busCallback
                    );
                }
                this._busCallback = null;
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

    return { eventHubService };
});
