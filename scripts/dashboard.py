"""
Truth Engine Streamlit Dashboard

Visualizes entity dependencies, conflicts, and entity timeline history.
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

import streamlit as st
import plotly.graph_objects as go
import networkx as nx
import pandas as pd

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.db.dolt_client import DoltClient
import pymysql

# Page configuration
st.set_page_config(
    page_title="Truth Engine Dashboard",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialize session state
if 'last_refresh' not in st.session_state:
    st.session_state.last_refresh = None


def load_entities() -> List[Dict[str, Any]]:
    """Load all entities from Dolt"""
    try:
        dolt_client = DoltClient()
        with dolt_client.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, name, entity_type, status, milestone_date, owner_team, metadata
                FROM project_entities
                ORDER BY milestone_date
            """)
            entities = cursor.fetchall()

            # Convert to list of dicts and handle metadata
            result = []
            for entity in entities:
                entity_dict = dict(entity)
                # metadata is already a dict with DictCursor, but handle string case
                if isinstance(entity_dict.get('metadata'), str):
                    entity_dict['metadata'] = json.loads(entity_dict['metadata'])
                result.append(entity_dict)

            return result
    except Exception as e:
        st.error(f"Error loading entities: {e}")
        import traceback
        st.error(traceback.format_exc())
        return []


def load_dependencies() -> List[Dict[str, Any]]:
    """Load all dependencies from Dolt"""
    try:
        dolt_client = DoltClient()
        with dolt_client.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT parent_id, child_id, dependency_type, confidence
                FROM dependencies
            """)
            deps = cursor.fetchall()
            return [dict(dep) for dep in deps]
    except Exception as e:
        st.error(f"Error loading dependencies: {e}")
        import traceback
        st.error(traceback.format_exc())
        return []


def load_notifications() -> List[Dict[str, Any]]:
    """Load notifications from notifications.json"""
    try:
        notifications_path = Path(__file__).parent.parent / "backend" / "notifications.json"
        if notifications_path.exists():
            with open(notifications_path, "r") as f:
                raw_notifications = json.load(f)

                # Handle both old flat format and new nested format
                notifications = []
                if isinstance(raw_notifications, list):
                    for notif in raw_notifications:
                        # If notification has 'data' key, extract it (new format)
                        if 'data' in notif and isinstance(notif['data'], dict):
                            # Transform to expected format
                            data = notif['data']
                            transformed = {
                                'conflict': {
                                    'conflict_id': data.get('conflict_id', 'unknown'),
                                    'description': f"{data.get('parent_entity', {}).get('name', 'Unknown')} depends on {data.get('child_entity', {}).get('name', 'Unknown')}",
                                    'severity': 'CRITICAL' if data.get('verdict') == 'CRITICAL_CONFLICT' else 'MEDIUM',
                                    'affected_entity_ids': [
                                        data.get('parent_entity', {}).get('id'),
                                        data.get('child_entity', {}).get('id')
                                    ]
                                },
                                'verdict': {
                                    'verdict': data.get('verdict'),
                                    'confidence': data.get('confidence', 0),
                                    'reasoning': data.get('reasoning', ''),
                                    'evidence_summary': '\n'.join([ev.get('summary', '') for ev in data.get('evidence', [])]),
                                    'recommendation': data.get('recommended_action', '')
                                },
                                'affected_teams': [
                                    data.get('parent_entity', {}).get('owner_team'),
                                    data.get('child_entity', {}).get('owner_team')
                                ],
                                'recommended_actions': [data.get('recommended_action', '')] if data.get('recommended_action') else [],
                                'timestamp': notif.get('timestamp', data.get('timestamp', ''))
                            }
                            notifications.append(transformed)
                        # Old flat format (keep for backwards compatibility)
                        elif 'conflict' in notif:
                            notifications.append(notif)

                return notifications
        return []
    except Exception as e:
        st.error(f"Error loading notifications: {e}")
        import traceback
        st.error(traceback.format_exc())
        return []


def get_entity_history(entity_id: str) -> List[Dict[str, Any]]:
    """Get commit history for a specific entity"""
    try:
        dolt_client = DoltClient()
        with dolt_client.get_connection() as conn:
            cursor = conn.cursor()

            # Query dolt_diff to get changes
            cursor.execute("""
                SELECT
                    from_id,
                    from_milestone_date,
                    to_id,
                    to_milestone_date,
                    from_status,
                    to_status,
                    from_commit,
                    to_commit,
                    from_commit_date,
                    to_commit_date
                FROM dolt_diff_project_entities
                WHERE to_id = %s OR from_id = %s
                ORDER BY to_commit_date ASC
            """, (entity_id, entity_id))

            history = cursor.fetchall()
            return [dict(h) for h in history]
    except Exception as e:
        st.warning(f"Could not load history for {entity_id}: {e}")
        import traceback
        st.error(traceback.format_exc())
        return []


def create_dependency_graph(entities: List[Dict], dependencies: List[Dict]) -> go.Figure:
    """Create interactive dependency graph using Plotly with hierarchical layout"""

    # Create NetworkX graph
    G = nx.DiGraph()

    # Add nodes
    entity_map = {e['id']: e for e in entities}
    for entity in entities:
        G.add_node(
            entity['id'],
            name=entity['name'],
            type=entity['entity_type'],
            status=entity['status'],
            owner=entity['owner_team'],
            date=entity['milestone_date']
        )

    # Add edges
    for dep in dependencies:
        G.add_edge(
            dep['child_id'],  # Dependency flows from child to parent
            dep['parent_id'],
            dep_type=dep['dependency_type'],
            confidence=dep['confidence']
        )

    # Create hierarchical layout based on entity type
    # Bottom to top: REQUIREMENT → PART → TEST → MILESTONE
    type_levels = {
        'REQUIREMENT': 0,
        'PART': 1,
        'TEST': 2,
        'MILESTONE': 3
    }

    # Calculate positions
    pos = {}
    level_counts = {0: 0, 1: 0, 2: 0, 3: 0}
    level_nodes = {0: [], 1: [], 2: [], 3: []}

    # Group nodes by level
    for node_id in G.nodes():
        entity = entity_map[node_id]
        level = type_levels.get(entity['entity_type'], 1)
        level_nodes[level].append(node_id)
        level_counts[level] += 1

    # Position nodes in each level
    for level, nodes in level_nodes.items():
        count = len(nodes)
        for i, node_id in enumerate(sorted(nodes)):
            # Spread nodes horizontally within their level
            x = (i - count/2) * 2.0  # Spacing of 2.0 units
            y = level * 3.0  # Vertical spacing of 3.0 units
            pos[node_id] = (x, y)

    # Define colors with better contrast and modern palette
    status_colors = {
        'COMPLETED': '#10b981',     # emerald green
        'ON_TRACK': '#3b82f6',      # bright blue
        'AT_RISK': '#f59e0b',       # amber
        'BLOCKED': '#ef4444',       # red
        'DELAYED': '#f97316',       # orange
        'CRITICAL': '#dc2626'       # dark red
    }

    type_shapes = {
        'PART': 'circle',
        'TEST': 'square',
        'MILESTONE': 'diamond',
        'REQUIREMENT': 'hexagon'
    }

    # Create edge traces
    edge_traces = []
    for edge in G.edges(data=True):
        x0, y0 = pos[edge[0]]
        x1, y1 = pos[edge[1]]

        dep_type = edge[2].get('dep_type', 'UNKNOWN')
        # BLOCKER = red/thick, INFORMATIONAL = gray/thin
        color = '#ef4444' if dep_type == 'CRITICAL_BLOCKER' else '#94a3b8'
        width = 3 if dep_type == 'CRITICAL_BLOCKER' else 1.5
        dash = 'solid' if dep_type == 'CRITICAL_BLOCKER' else 'dash'

        edge_trace = go.Scatter(
            x=[x0, x1, None],
            y=[y0, y1, None],
            mode='lines',
            line=dict(width=width, color=color, dash=dash),
            hoverinfo='text',
            text=f"<b>{edge[0]} → {edge[1]}</b><br>Type: {dep_type}<br>Confidence: {edge[2].get('confidence', 'N/A')}",
            showlegend=False,
            opacity=0.8 if dep_type == 'CRITICAL_BLOCKER' else 0.5
        )
        edge_traces.append(edge_trace)

    # Create node trace
    node_x = []
    node_y = []
    node_text = []
    node_color = []
    node_size = []

    for node in G.nodes():
        x, y = pos[node]
        node_x.append(x)
        node_y.append(y)

        entity = entity_map[node]

        # Hover text
        hover_text = f"<b>{entity['name']}</b><br>"
        hover_text += f"ID: {entity['id']}<br>"
        hover_text += f"Type: {entity['entity_type']}<br>"
        hover_text += f"Status: {entity['status']}<br>"
        hover_text += f"Date: {entity['milestone_date']}<br>"
        hover_text += f"Owner: {entity['owner_team']}"
        node_text.append(hover_text)

        # Color by status
        node_color.append(status_colors.get(entity['status'], '#6c757d'))

        # Size by type (milestones larger)
        size = 30 if entity['entity_type'] == 'MILESTONE' else 20
        node_size.append(size)

    node_trace = go.Scatter(
        x=node_x,
        y=node_y,
        mode='markers+text',
        hoverinfo='text',
        text=[entity_map[n]['id'] for n in G.nodes()],
        textposition="top center",
        textfont=dict(size=9, family='Arial, sans-serif', color='#1f2937'),
        hovertext=node_text,
        marker=dict(
            size=node_size,
            color=node_color,
            line=dict(width=2.5, color='#ffffff'),
            opacity=0.9
        ),
        showlegend=False
    )

    # Create figure
    fig = go.Figure(data=edge_traces + [node_trace])

    fig.update_layout(
        title=dict(
            text="Entity Dependency Graph (Hierarchical: Requirements → Parts → Tests → Milestones)",
            font=dict(size=18, family='Arial, sans-serif', color='#111827')
        ),
        showlegend=False,
        hovermode='closest',
        margin=dict(b=40, l=40, r=40, t=80),
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False, scaleanchor="x", scaleratio=1),
        height=800,
        plot_bgcolor='#f8fafc',
        paper_bgcolor='#ffffff'
    )

    return fig


def render_conflict_alerts(notifications: List[Dict]):
    """Render conflict alerts panel"""
    st.subheader("Conflict Alerts")

    if not notifications:
        st.info("No conflicts detected. System is in a consistent state.")
        return

    for notif in notifications:
        conflict = notif.get('conflict', {})
        verdict = notif.get('verdict', {})

        # Severity badge
        severity = conflict.get('severity', 'UNKNOWN')
        severity_colors = {
            'CRITICAL': '🔴',
            'HIGH': '🟠',
            'MEDIUM': '🟡',
            'LOW': '🟢'
        }
        severity_icon = severity_colors.get(severity, '⚪')

        with st.expander(f"{severity_icon} {conflict.get('description', 'Unknown conflict')}", expanded=True):

            # Conflict details
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**Affected Entities:**")
                for entity_id in conflict.get('affected_entity_ids', []):
                    st.markdown(f"- `{entity_id}`")

            with col2:
                st.markdown("**Teams Affected:**")
                for team in notif.get('affected_teams', []):
                    st.markdown(f"- {team}")

            # Evidence snippets
            if verdict and verdict.get('evidence_summary'):
                st.markdown("**Evidence:**")
                st.info(verdict['evidence_summary'])

            # Judge verdict
            if verdict and verdict.get('recommendation'):
                st.markdown("**Judge Recommendation:**")
                st.success(verdict['recommendation'])

            # Recommended actions
            if notif.get('recommended_actions'):
                st.markdown("**Recommended Actions:**")
                for action in notif['recommended_actions']:
                    st.markdown(f"- {action}")

            # Timestamp
            st.caption(f"Detected: {notif.get('timestamp', 'Unknown')}")


def render_entity_timeline():
    """Render entity timeline visualization"""
    st.subheader("Entity Timeline")

    entities = load_entities()
    if not entities:
        st.warning("No entities found in database")
        return

    # Entity selector
    entity_ids = [e['id'] for e in entities]
    selected_entity = st.selectbox(
        "Select entity to view history:",
        options=entity_ids,
        format_func=lambda x: f"{x} - {next((e['name'] for e in entities if e['id'] == x), x)}"
    )

    if not selected_entity:
        return

    # Get history
    history = get_entity_history(selected_entity)

    if not history:
        st.info(f"No commit history found for {selected_entity}. Entity timeline will populate as entities are updated over time.")
        return

    # Create timeline dataframe - show ALL changes (status OR date)
    timeline_data = []
    for change in history:
        # Check if status changed
        status_changed = change.get('from_status') != change.get('to_status')

        # Check if date changed
        date_changed = (
            change.get('from_milestone_date') and
            change.get('to_milestone_date') and
            change['from_milestone_date'] != change['to_milestone_date']
        )

        # Show entry if EITHER status or date changed
        if status_changed or date_changed:
            timeline_data.append({
                'Commit Date': change['to_commit_date'],
                'Status Change': f"{change.get('from_status', 'N/A')} → {change.get('to_status', 'N/A')}",
                'Date Change': f"{change.get('from_milestone_date', 'N/A')} → {change.get('to_milestone_date', 'N/A')}" if date_changed else 'No change',
                'Commit': change.get('to_commit', 'N/A')[:8] if change.get('to_commit') else 'N/A'
            })

    if timeline_data:
        df = pd.DataFrame(timeline_data)
        st.dataframe(df, width='stretch')

        # Visualize timeline with status and date changes
        st.markdown("### Change History Visualization")

        # Show only rows with actual date changes for visualization
        date_change_rows = [row for idx, row in df.iterrows() if row['Date Change'] != 'No change']

        if date_change_rows:
            fig = go.Figure()

            # Parse dates for visualization
            dates = []
            labels = []
            for row in date_change_rows:
                parts = row['Date Change'].split(' → ')
                if len(parts) == 2:
                    dates.append(parts[0])
                    labels.append(f"From: {parts[0]}")
                    dates.append(parts[1])
                    labels.append(f"To: {parts[1]}")

            if dates:
                fig.add_trace(go.Scatter(
                    x=list(range(len(dates))),
                    y=dates,
                    mode='lines+markers',
                    name='Milestone Date',
                    text=labels,
                    marker=dict(size=10, color='#3b82f6'),
                    line=dict(width=2, color='#3b82f6')
                ))

                fig.update_layout(
                    title=f"Milestone Date Changes for {selected_entity}",
                    xaxis_title="Change Number",
                    yaxis_title="Date",
                    height=400,
                    plot_bgcolor='#f8fafc'
                )

                st.plotly_chart(fig, width='stretch')
        else:
            st.info("No milestone date changes in this history. Only status changes were recorded.")
    else:
        st.info("No changes detected in history. Timeline will populate as entities are updated through the workflow.")


def main():
    """Main dashboard application"""

    # Header
    st.title("🔍 Truth Engine Dashboard")
    st.markdown("Real-time visualization of entity dependencies, conflicts, and version history")

    # Sidebar
    with st.sidebar:
        st.header("Controls")

        if st.button("🔄 Refresh Data", width='stretch'):
            st.session_state.last_refresh = datetime.now()
            st.rerun()

        if st.session_state.last_refresh:
            st.caption(f"Last refresh: {st.session_state.last_refresh.strftime('%H:%M:%S')}")

        st.divider()

        st.header("System Status")
        try:
            dolt_client = DoltClient()
            with dolt_client.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) as count FROM project_entities")
                entity_count = cursor.fetchone()['count']
                cursor.execute("SELECT COUNT(*) as count FROM dependencies")
                dep_count = cursor.fetchone()['count']

                st.metric("Entities", entity_count)
                st.metric("Dependencies", dep_count)

                notifications = load_notifications()
                conflict_count = len(notifications)
                st.metric("Active Conflicts", conflict_count, delta=None if conflict_count == 0 else "⚠️")
        except Exception as e:
            st.error(f"Database connection error: {e}")

    # Main content tabs
    tab1, tab2, tab3 = st.tabs(["📊 Dependency Graph", "⚠️ Conflict Alerts", "📈 Entity Timeline"])

    with tab1:
        entities = load_entities()
        dependencies = load_dependencies()

        if entities and dependencies:
            fig = create_dependency_graph(entities, dependencies)
            st.plotly_chart(fig, width='stretch')

            # Legend with improved styling
            st.markdown("### Legend")
            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown("**Status Colors:**")
                st.markdown("""
                - 🟢 **COMPLETED** - Done
                - 🔵 **ON_TRACK** - Progressing normally
                - 🟡 **AT_RISK** - Potential issues
                - 🔴 **BLOCKED** - Cannot proceed
                - 🟠 **DELAYED** - Behind schedule
                - ⚫ **CRITICAL** - Urgent attention needed
                """)
            with col2:
                st.markdown("**Dependency Types:**")
                st.markdown("""
                - **━━ BLOCKER** (red, solid, thick)
                  Must complete before parent
                - **┄┄ INFORMATIONAL** (gray, dashed, thin)
                  Reference/context only
                """)
            with col3:
                st.markdown("**Layout:**")
                st.markdown("""
                **Bottom → Top:**
                1. REQUIREMENTS
                2. PARTS
                3. TESTS
                4. MILESTONES

                *Larger nodes = Milestones*
                """)
        else:
            st.warning("No entity or dependency data available. Please run the workflow first.")

    with tab2:
        notifications = load_notifications()
        render_conflict_alerts(notifications)

    with tab3:
        render_entity_timeline()


if __name__ == "__main__":
    main()
