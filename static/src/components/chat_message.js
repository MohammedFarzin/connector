odoo.define("connector.components.chat_message", function (require) {
    "use strict";

    /**
     * ChatMessage component — renders a single message bubble in the chat.
     *
     * Props:
     *   - message: {
     *       role: 'user'|'assistant',
     *       content: string,
     *       html: string|null,
     *       isHtml: boolean,
     *       structuredData: object|null,  // NEW: for meeting cards / team overview
     *     }
     *
     * Sub-components: MeetingCard, TeamOverviewTable
     *   - Rendered conditionally based on structuredData.display_type
     */

    const { Component } = require("@odoo/owl");
    const { MeetingCard } = require("connector.components.meeting_card");
    const { TeamOverviewTable } = require("connector.components.team_overview_table");

    class ChatMessage extends Component {
        static template = "crm_assistant.ChatMessage";
        static components = { MeetingCard, TeamOverviewTable };
        static props = {
            message: Object,
        };

        /**
         * Returns true if this message was sent by the user.
         */
        get isUser() {
            return this.props.message.role === "user";
        }
    }

    return { ChatMessage };
});
