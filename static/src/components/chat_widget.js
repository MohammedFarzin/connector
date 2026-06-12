/** @odoo-module **/

/**
 * FloatingWidget — the main chatbot parent component.
 *
 * Manages the complete chat experience:
 *   - Toggle open/close state (floating button + slide-up panel)
 *   - Message history (persisted via server session + localStorage fallback)
 *   - Communication with backend API (/crm_assistant_connector/message, /reset, /restore)
 *   - Loading states, error handling, auto-scroll
 *   - Real-time updates via EventHub bus subscription
 *
 * Child components: ChatMessage, ChatInput
 *
 * UI: Fixed positioned bottom-right, ~24px margin.
 *     Round toggle button when closed, 380x560px slide-up panel when open.
 */

import { Component, useState, useRef, onMounted, onWillDestroy, markup, useService } from "@odoo/owl";
import { ChatMessage } from "./chat_message";
import { ChatInput } from "./chat_input";
import { rpc } from "@web/core/network/rpc";

const LOCAL_STORAGE_SESSION_KEY = "crm_assistant_session_id";

export class FloatingWidget extends Component {
    static template = "crm_assistant.FloatingWidget";
    static components = { ChatMessage, ChatInput };
    static props = {};

    setup() {
        this.messagesContainerRef = useRef("messagesContainer");

        this.state = useState({
            isOpen: false,
            messages: [],
            loading: false,
            restoring: false,
            showHistory: false,
            sessions: [],
            sessionsLoading: false,
            error: null,
        });

        // Session restore runs on mount (async, with loading indicator)
        onMounted(() => {
            this._initSession();
        });

        // Subscribe to real-time event hub
        this._setupEventHub();

        // Cleanup debounce timer and event subscriptions on destroy
        onWillDestroy(() => {
            if (this._reloadTimer) {
                clearTimeout(this._reloadTimer);
                this._reloadTimer = null;
            }
            if (this._eventHub && this._recordChangedHandler) {
                this._eventHub.off("record_changed", this._recordChangedHandler);
                this._recordChangedHandler = null;
            }
        });
    }

    /**
     * Initialize session: restore from server if localStorage has a session ID,
     * otherwise generate a fresh one. Runs on mount.
     */
    async _initSession() {
        const storedId = this._getStoredSessionId();
        if (storedId) {
            this.state.restoring = true;
            try {
                await this._restoreSession(storedId);
            } finally {
                this.state.restoring = false;
            }
        }

        // Ensure we always have a session ID
        if (!this.sessionId) {
            this.sessionId = this._generateSessionId();
        }
    }

    /**
     * Toggle the chat panel open/closed.
     * Shows welcome message on first open.
     */
    toggle() {
        this.state.isOpen = !this.state.isOpen;

        // Close history panel when closing the chat
        if (!this.state.isOpen) {
            this.state.showHistory = false;
        }

        if (this.state.isOpen && this.state.messages.length === 0 && !this.state.restoring) {
            this.state.messages.push({
                role: "assistant",
                content: (
                    "👋 Hi! I'm your CRM assistant. I can help you create leads, "
                    + "search your pipeline, move deals through stages, and answer "
                    + "questions about your CRM. What would you like to do?"
                ),
            });
        }

        if (this.state.isOpen) {
            this._scrollToBottom();
        }
    }

    /**
     * Handle a user sending a message.
     * Called by the ChatInput child component.
     *
     * @param {string} text - the user's message text
     */
    async onSendMessage(text) {
        if (!text || this.state.loading || this.state.restoring) {
            return;
        }

        this.state.messages.push({ role: "user", content: text });
        this.state.loading = true;
        this.state.error = null;
        this._scrollToBottom();

        try {
            const result = await rpc("/crm_assistant_connector/message", {
                message: text,
                session_id: this.sessionId,
                history: this.state.messages.slice(0, -1),
            });

            if (result.error) {
                this.state.messages.push({
                    role: "assistant",
                    content: result.error,
                });
            } else {
                this.state.messages.push({
                    role: "assistant",
                    content: result.text,
                    html: markup(result.html),
                    isHtml: !!result.html,
                    structuredData: result.structured_data || null,
                });

                if (result.session_id) {
                    this.sessionId = result.session_id;
                    this._storeSessionId(result.session_id);
                }

                // Process structured data for immediate card updates
                if (result.structured_data) {
                    this._processStructuredData(result.structured_data);
                }

                // Reload page if the backend performed any writes.
                // This is the primary delivery mechanism — more reliable
                // than the bus notification (which is a secondary best-effort).
                if (result.reload) {
                    console.debug("[ChatWidget] Backend performed writes — reloading page");
                    setTimeout(() => window.location.reload(), 800);
                }
            }

        } catch (err) {
            console.error("CRM Assistant API error:", err);
            this.state.messages.push({
                role: "assistant",
                content: (
                    "Sorry, I couldn't reach the service. "
                    + "Please check your connection and try again."
                ),
            });

        } finally {
            this.state.loading = false;
            this._scrollToBottom();
        }
    }

    /**
     * Reset the session — clear all history and start fresh.
     * Called when the user clicks "New conversation" in the header.
     */
    async onResetSession() {
        try {
            const result = await rpc("/crm_assistant_connector/reset", {
                session_id: this.sessionId,
            });
            if (result.session_id) {
                this.sessionId = result.session_id;
                this._storeSessionId(result.session_id);
            }
        } catch (err) {
            console.warn("Failed to reset server session:", err);
        }

        this.state.messages = [];
        // Clear stored session ID on reset
        this._clearStoredSessionId();
        // Generate a fresh fallback (server already returned new ID above)
        if (!this.sessionId) {
            this.sessionId = this._generateSessionId();
        }

        this.state.messages.push({
            role: "assistant",
            content: "🔄 Conversation reset. How can I help?",
        });
    }

    // =========================================================================
    // SESSION PERSISTENCE
    // =========================================================================

    /**
     * Get the stored session ID from localStorage.
     * @returns {string|null}
     */
    _getStoredSessionId() {
        try {
            return localStorage.getItem(LOCAL_STORAGE_SESSION_KEY);
        } catch (_e) {
            return null;
        }
    }

    /**
     * Store the session ID in localStorage for page-reload persistence.
     * @param {string} sessionId
     */
    _storeSessionId(sessionId) {
        try {
            localStorage.setItem(LOCAL_STORAGE_SESSION_KEY, sessionId);
        } catch (_e) {
            // localStorage full or unavailable — non-critical
        }
    }

    /**
     * Remove the stored session ID from localStorage.
     */
    _clearStoredSessionId() {
        try {
            localStorage.removeItem(LOCAL_STORAGE_SESSION_KEY);
        } catch (_e) {
            // Ignore
        }
    }

    /**
     * Attempt to restore a previous session from the server.
     * Loads message history into state if the session still exists.
     *
     * @param {string} sessionId
     */
    async _restoreSession(sessionId) {
        try {
            const result = await rpc("/crm_assistant_connector/restore", {
                session_id: sessionId,
            });

            if (result.found && result.messages.length > 0) {
                this.sessionId = result.session_id;
                this._storeSessionId(result.session_id);

                // Push restored messages with full data (html, structured_data)
                for (const msg of result.messages) {
                    this.state.messages.push({
                        role: msg.role,
                        content: msg.content,
                        html: msg.html ? markup(msg.html) : null,
                        isHtml: !!msg.html,
                        structuredData: msg.structured_data || null,
                    });
                }
            } else {
                // Session expired or not found — start fresh
                this._clearStoredSessionId();
            }
        } catch (err) {
            console.warn("Failed to restore session:", err);
            this._clearStoredSessionId();
            // Continue with a fresh session
        }
    }

    // =========================================================================
    // CHAT HISTORY PANEL
    // =========================================================================

    /**
     * Toggle the chat history sessions list panel.
     */
    async onToggleHistory() {
        this.state.showHistory = !this.state.showHistory;

        if (this.state.showHistory) {
            await this._loadSessions();
        }
    }

    /**
     * Fetch the list of past sessions from the backend.
     */
    async _loadSessions() {
        this.state.sessionsLoading = true;
        try {
            const result = await rpc("/crm_assistant_connector/sessions", {});
            this.state.sessions = result.sessions || [];
        } catch (err) {
            console.warn("Failed to load sessions:", err);
            this.state.sessions = [];
        } finally {
            this.state.sessionsLoading = false;
        }
    }

    /**
     * Select a past session and restore its messages.
     *
     * @param {string} sessionId
     */
    async onSelectSession(sessionId) {
        if (!sessionId || sessionId === this.sessionId) {
            return;
        }

        // Clear current messages and show loading
        this.state.messages = [];
        this.state.restoring = true;
        this.state.showHistory = false;

        try {
            // Switch session ID and restore
            this.sessionId = sessionId;
            await this._restoreSession(sessionId);
            this._scrollToBottom();
        } finally {
            this.state.restoring = false;
        }
    }

    /**
     * Format a session's updated_at timestamp for display.
     *
     * @param {string} isoString
     * @returns {string} human-readable relative time
     */
    _formatSessionTime(isoString) {
        if (!isoString) return '';
        try {
            const date = new Date(isoString);
            const now = new Date();
            const diffMs = now - date;
            const diffMin = Math.floor(diffMs / 60000);
            const diffHours = Math.floor(diffMs / 3600000);
            const diffDays = Math.floor(diffMs / 86400000);

            if (diffMin < 1) return 'Just now';
            if (diffMin < 60) return `${diffMin}m ago`;
            if (diffHours < 24) return `${diffHours}h ago`;
            if (diffDays < 7) return `${diffDays}d ago`;
            return date.toLocaleDateString();
        } catch (_e) {
            return '';
        }
    }

    // =========================================================================
    // REAL-TIME EVENT HUB
    // =========================================================================

    /**
     * Subscribe to the EventHub for real-time updates from write operations.
     * When a tool execution dispatches a bus notification, the EventHub
     * calls our handlers so we can update cards/messages instantly.
     */
    _setupEventHub() {
        try {
            const eventHub = useService("crm_assistant_event_hub");
            if (eventHub) {
                eventHub.start();
                this._eventHub = eventHub;

                // Subscribe to each event type we care about
                this._eventHub.on("meeting_cancelled", this._onMeetingCancelled.bind(this));
                this._eventHub.on("meeting_scheduled", this._onMeetingScheduled.bind(this));
                this._eventHub.on("meeting_rescheduled", this._onMeetingRescheduled.bind(this));
                this._eventHub.on("lead_stage_changed", this._onLeadStageChanged.bind(this));
                this._eventHub.on("lead_status_changed", this._onLeadStatusChanged.bind(this));
                // Record change → auto-refresh Odoo views
                this._recordChangedHandler = this._onRecordChanged.bind(this);
                this._eventHub.on("record_changed", this._recordChangedHandler);
            }
        } catch (_e) {
            // EventHub service may not be available (e.g. bus not installed)
            console.debug("EventHub not available — real-time updates disabled");
        }
    }

    /**
     * Process structured data from the immediate API response.
     * Updates message cards instantly without waiting for the bus.
     *
     * @param {Object} structuredData - from API response
     */
    _processStructuredData(structuredData) {
        if (!structuredData?.display_type) return;

        switch (structuredData.display_type) {
            case "meeting_cancelled":
                this._removeMeetingFromCards(structuredData.meeting_id);
                break;
            case "meeting_scheduled":
                this._addMeetingToCards(structuredData);
                break;
            case "meeting_rescheduled":
                this._updateMeetingInCards(structuredData);
                break;
            case "lead_stage_changed":
                this._updateLeadStageInMessages(structuredData);
                break;
            case "lead_status_changed":
                this._updateLeadStatusInMessages(structuredData);
                break;
        }
    }

    // ----- Event Hub Callbacks (bus notifications from backend) -----

    _onMeetingCancelled(data) {
        this._removeMeetingFromCards(data.meeting_id);
    }

    _onMeetingScheduled(data) {
        this._addMeetingToCards(data);
    }

    _onMeetingRescheduled(data) {
        this._updateMeetingInCards(data);
    }

    _onLeadStageChanged(data) {
        this._updateLeadStageInMessages(data);
    }

    _onLeadStatusChanged(data) {
        this._updateLeadStatusInMessages(data);
    }

    /**
     * Handle record_changed event from the EventHub.
     *
     * Triggered when the backend dispatches a bus notification after a
     * successful write/create/unlink via the chat. Uses a 1.5s debounce
     * to coalesce multiple notifications from multi-step instruction sets
     * into a single page reload.
     *
     * @param {Object} data - { model, record_ids, method }
     */
    _onRecordChanged(data) {
        if (!data || !data.model) return;

        console.debug(
            "[ChatWidget] Record changed:", data.model, data.method,
            "— scheduling page reload"
        );

        // Debounce: clear any pending reload and set a new one.
        // Multiple notifications in rapid succession → single reload.
        if (this._reloadTimer) {
            clearTimeout(this._reloadTimer);
        }
        this._reloadTimer = setTimeout(() => {
            console.debug("[ChatWidget] Reloading page to reflect record changes");
            window.location.reload();
        }, 1500);
    }

    // ----- Structured Data Handlers -----

    /**
     * Remove a meeting card from all messages by meeting_id.
     * @param {number} meetingId
     */
    _removeMeetingFromCards(meetingId) {
        if (!meetingId) return;

        for (const msg of this.state.messages) {
            const sd = msg.structuredData;
            if (sd?.display_type === "meeting_list" && sd.meetings) {
                sd.meetings = sd.meetings.filter((m) => m.id !== meetingId);
                // Update count
                sd.count = sd.meetings.length;
            }
        }
    }

    /**
     * Add a meeting to existing meeting_list cards if the lead matches.
     * @param {Object} data - { meeting_id, lead_id, lead_name, meeting_date }
     */
    _addMeetingToCards(data) {
        // This is a lightweight hint — full meeting data requires a re-fetch.
        // For now, trigger a subtle refresh hint in the UI.
        if (!data.meeting_id) return;

        // Find meeting_list messages and mark them for refresh
        for (const msg of this.state.messages) {
            if (msg.structuredData?.display_type === "meeting_list") {
                msg.structuredData._needsRefresh = true;
            }
        }
    }

    /**
     * Update a meeting card in all messages by meeting_id.
     * @param {Object} data - { meeting_id, old_date, new_date }
     */
    _updateMeetingInCards(data) {
        if (!data.meeting_id) return;

        for (const msg of this.state.messages) {
            const sd = msg.structuredData;
            if (sd?.display_type === "meeting_list" && sd.meetings) {
                for (const m of sd.meetings) {
                    if (m.id === data.meeting_id) {
                        // Update with new date info
                        if (data.new_date) {
                            m.start = data.new_date;
                        }
                        m._updated = true;
                    }
                }
            }
        }
    }

    /**
     * Update lead stage references in message text.
     * @param {Object} data - { lead_id, lead_name, old_stage, new_stage }
     */
    _updateLeadStageInMessages(data) {
        // For now, stage changes are reflected in the conversation flow.
        // Future: could highlight the affected lead name in previous messages.
        if (!data.lead_id) return;

        for (const msg of this.state.messages) {
            const sd = msg.structuredData;
            // Update search result cards if they reference this lead
            if (sd?.results) {
                for (const r of sd.results) {
                    if (r.id === data.lead_id && data.new_stage) {
                        r.stage = data.new_stage;
                    }
                }
            }
        }
    }

    /**
     * Update lead status (won/lost) in message data.
     * @param {Object} data - { lead_id, lead_name, status }
     */
    _updateLeadStatusInMessages(data) {
        if (!data.lead_id) return;

        const statusLabel = data.status === "won" ? "Won" : "Lost";

        for (const msg of this.state.messages) {
            const sd = msg.structuredData;
            // Update search result cards
            if (sd?.results) {
                for (const r of sd.results) {
                    if (r.id === data.lead_id) {
                        r._status = statusLabel;
                    }
                }
            }
            // Update lead detail view
            if (sd?.lead?.id === data.lead_id) {
                sd.lead._status = statusLabel;
            }
        }
    }

    // =========================================================================
    // UTILITIES
    // =========================================================================

    _generateSessionId() {
        return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
            const r = (Math.random() * 16) | 0;
            const v = c === "x" ? r : (r & 0x3) | 0x8;
            return v.toString(16);
        });
    }

    _scrollToBottom() {
        setTimeout(() => {
            const container = this.messagesContainerRef.el;
            if (container) {
                container.scrollTop = container.scrollHeight;
            }
        }, 50);
    }
}
