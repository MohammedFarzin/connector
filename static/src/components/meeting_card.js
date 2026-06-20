odoo.define("connector.components.meeting_card", function (require) {
    "use strict";

    /**
     * MeetingCard component — renders a single meeting as a styled card.
     *
     * Props:
     *   - meeting: {
     *       id: number,
     *       name: string,
     *       time: string,          // "09:00 - 10:00"
     *       duration: number,      // hours
     *       status: string,        // "upcoming" | "in-progress" | "past"
     *       lead_name: string,
     *       lead_url: string,      // "#id=X&model=crm.lead&view_type=form"
     *       user_name: string,
     *       attendees: string[],
     *     }
     */

    const { Component } = require("@odoo/owl");

    class MeetingCard extends Component {
        static template = "crm_assistant.MeetingCard";
        static props = {
            meeting: Object,
        };

        /**
         * CSS class for the left accent bar, determined by meeting status.
         */
        get statusClass() {
            const status = this.props.meeting.status;
            if (status === "upcoming") return "meeting-card--upcoming";
            if (status === "in-progress") return "meeting-card--in-progress";
            return "meeting-card--past";
        }

        /**
         * Human-readable status label.
         */
        get statusLabel() {
            const status = this.props.meeting.status;
            if (status === "upcoming") return "Upcoming";
            if (status === "in-progress") return "In Progress";
            return "Past";
        }

        /**
         * Human-readable duration string.
         */
        get durationLabel() {
            const hours = this.props.meeting.duration;
            if (!hours) return "";
            if (hours >= 1) {
                const h = Math.floor(hours);
                const m = Math.round((hours - h) * 60);
                return m > 0 ? `${h}h ${m}m` : `${h}h`;
            }
            return `${Math.round(hours * 60)}m`;
        }

        /**
         * Click handler: opens the linked lead record.
         */
        openLead() {
            const url = this.props.meeting.lead_url;
            if (url) {
                window.location.hash = url.replace("#", "");
            }
        }
    }

    return { MeetingCard };
});
