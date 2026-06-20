odoo.define("connector.components.chat_input", function (require) {
    "use strict";

    /**
     * ChatInput component — text input area with send button.
     *
     * Features:
     *   - Enter to send, Shift+Enter for newline
     *   - Auto-disables when loading (waiting for backend response)
     *   - Prevents sending empty messages
     *
     * Emits via callback:
     *   - onSend(text): fired when user sends a message
     */

    const { Component, useState } = require("@odoo/owl");

    class ChatInput extends Component {
        static template = "crm_assistant.ChatInput";
        static props = {
            loading: { type: Boolean, optional: true },
            disabled: { type: Boolean, optional: true },
            onSend: Function,
        };

        setup() {
            this.state = useState({
                text: "",
            });
        }

        /**
         * Handle keyboard events.
         * Enter alone → send message.
         * Shift+Enter → newline (default browser behavior, not intercepted).
         */
        onKeydown(event) {
            if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                this.send();
            }
        }

        /**
         * Send the current message text to the parent component.
         * Clears the input field after sending.
         */
        send() {
            if (this.props.disabled) {
                return;
            }

            const text = this.state.text.trim();
            if (!text) {
                return;
            }

            this.props.onSend(text);
            this.state.text = "";
        }
    }

    return { ChatInput };
});
