/** @odoo-module **/

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

import { Component } from "@odoo/owl";
import { MeetingCard } from "./meeting_card";
import { TeamOverviewTable } from "./team_overview_table";

export class ChatMessage extends Component {
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
