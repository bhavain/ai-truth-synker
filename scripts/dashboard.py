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

            # Convert metadata JSON string to dict
            for entity in entities:
                if isinstance(entity['metadata'], str):
                    entity['metadata'] = json.loads(entity['metadata'])

            return entities
    except Exception as e:
        st.error(f"Error loading entities: {e}")
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
            return cursor.fetchall()
    except Exception as e:
        st.error(f"Error loading dependencies: {e}")
        return []


def load_notifications() -> List[Dict[str, Any]]:
    """Load notifications from notifications.json"""
    try:
        notifications_path = Path(__file__).parent.parent / "notifications.json"
        if notifications_path.exists():
            with open(notifications_path, "r") as f:
                notifications = json.load(f)
                return notifications if isinstance(notifications, list) else []
        return []
    except Exception as e:
        st.error(f"Error loading notifications: {e}")
        return []


def get_entity_history(entity_id: str) -> List[Dict[str, Any]]:
    """Get commit history for a specific entity"""
    try:
        dolt_client = DoltClient()
        with dolt_client.get_connection() as conn:
            cursor = conn.cursor()

            # Query dolt_diff to get changes
            cursor.execute(f"""
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

            return cursor.fetchall()
    except Exception as e:
        st.warning(f"Could not load history for {entity_id}: {e}")
        return []


def create_dependency_graph(entities: List[Dict], dependencies: List[Dict]) -> go.Figure:
    """Create interactive dependency graph using Plotly"""

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

    # Use spring layout for positioning
    pos = nx.spring_layout(G, k=2, iterations=50, seed=42)

    # Define colors
    status_colors = {
        'COMPLETED': '#28a745',     # green
        'ON_TRACK': '#17a2b8',      # blue
        'AT_RISK': '#ffc107',       # yellow
        'BLOCKED': '#dc3545',       # red
        'IN_PROGRESS': '#fd7e14'    # orange
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
        color = '#dc3545' if dep_type == 'CRITICAL_BLOCKER' else '#6c757d'
        width = 3 if dep_type == 'CRITICAL_BLOCKER' else 1

        edge_trace = go.Scatter(
            x=[x0, x1, None],
            y=[y0, y1, None],
            mode='lines',
            line=dict(width=width, color=color),
            hoverinfo='text',
            text=f"{edge[0]} → {edge[1]}<br>Type: {dep_type}",
            showlegend=False
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
        textfont=dict(size=8),
        hovertext=node_text,
        marker=dict(
            size=node_size,
            color=node_color,
            line=dict(width=2, color='white')
        ),
        showlegend=False
    )

    # Create figure
    fig = go.Figure(data=edge_traces + [node_trace])

    fig.update_layout(
        title="Entity Dependency Graph",
        showlegend=False,
        hovermode='closest',
        margin=dict(b=20, l=5, r=5, t=40),
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        height=600,
        plot_bgcolor='rgba(240,240,240,0.5)'
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
        st.info(f"No commit history found for {selected_entity}")
        return

    # Create timeline dataframe
    timeline_data = []
    for change in history:
        if change['from_milestone_date'] and change['to_milestone_date']:
            if change['from_milestone_date'] != change['to_milestone_date']:
                timeline_data.append({
                    'Commit Date': change['to_commit_date'],
                    'Previous Date': change['from_milestone_date'],
                    'New Date': change['to_milestone_date'],
                    'Status Change': f"{change.get('from_status', 'N/A')} → {change.get('to_status', 'N/A')}"
                })

    if timeline_data:
        df = pd.DataFrame(timeline_data)
        st.dataframe(df, use_container_width=True)

        # Visualize date changes
        fig = go.Figure()

        dates = []
        labels = []
        for idx, row in df.iterrows():
            dates.append(row['Previous Date'])
            labels.append(f"Previous: {row['Previous Date']}")
            dates.append(row['New Date'])
            labels.append(f"Updated: {row['New Date']}")

        if dates:
            fig.add_trace(go.Scatter(
                x=list(range(len(dates))),
                y=dates,
                mode='lines+markers',
                name='Milestone Date',
                text=labels,
                marker=dict(size=10)
            ))

            fig.update_layout(
                title=f"Milestone Date Changes for {selected_entity}",
                xaxis_title="Change Number",
                yaxis_title="Date",
                height=400
            )

            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No milestone date changes detected in history")


def main():
    """Main dashboard application"""

    # Header
    st.title("🔍 Truth Engine Dashboard")
    st.markdown("Real-time visualization of entity dependencies, conflicts, and version history")

    # Sidebar
    with st.sidebar:
        st.header("Controls")

        if st.button("🔄 Refresh Data", use_container_width=True):
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
                entity_count = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) as count FROM dependencies")
                dep_count = cursor.fetchone()[0]

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
            st.plotly_chart(fig, use_container_width=True)

            # Legend
            st.markdown("### Legend")
            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown("**Status Colors:**")
                st.markdown("🟢 COMPLETED | 🔵 ON_TRACK | 🟡 AT_RISK | 🔴 BLOCKED | 🟠 IN_PROGRESS")
            with col2:
                st.markdown("**Entity Types:**")
                st.markdown("● PART | ■ TEST | ◆ MILESTONE | ⬡ REQUIREMENT")
            with col3:
                st.markdown("**Edge Types:**")
                st.markdown("━━ CRITICAL_BLOCKER (red)")
                st.markdown("─ RECOMMENDED (gray)")
        else:
            st.warning("No entity or dependency data available. Please run the workflow first.")

    with tab2:
        notifications = load_notifications()
        render_conflict_alerts(notifications)

    with tab3:
        render_entity_timeline()


if __name__ == "__main__":
    main()
