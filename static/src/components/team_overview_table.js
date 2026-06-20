odoo.define("connector.components.team_overview_table", function (require) {
    "use strict";

    /**
     * TeamOverviewTable component — renders a manager dashboard table
     * showing all team members' meetings for a given date.
     *
     * Props:
     *   - data: {
     *       team_name: string,
     *       date: string,
     *       members: [{
     *         name: string,
     *         user_id: number,
     *         meetings_count: number,
     *         meetings: [{ id, subject, time, lead, status }],
     *       }],
     *       total_meetings: number,
     *     }
     *
     * Features:
     *   - Summary stats bar (total meetings, active members)
     *   - Expandable rows to show meeting details per member
     *   - Color-coded meeting count badges
     */

    const { Component, useState } = require("@odoo/owl");

    class TeamOverviewTable extends Component {
        static template = "crm_assistant.TeamOverviewTable";
        static props = {
            data: Object,
        };

        setup() {
            // Track which rows are expanded (keyed by user_id)
            this.state = useState({
                expandedRows: {},
            });
        }

        toggleRow(userId) {
            this.state.expandedRows[userId] = !this.state.expandedRows[userId];
        }

        get activeMembers() {
            return this.props.data.members.filter(m => m.meetings_count > 0).length;
        }

        get busiestMember() {
            if (!this.props.data.members.length) return "";
            return this.props.data.members[0].name;
        }

        getBadgeClass(count) {
            if (count >= 5) return "team-overview__badge--busy";
            if (count >= 3) return "team-overview__badge--moderate";
            if (count >= 1) return "team-overview__badge--light";
            return "team-overview__badge--none";
        }
    }

    return { TeamOverviewTable };
});
