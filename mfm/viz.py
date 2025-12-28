import streamlit as st
import plotly.graph_objects as go

def _shorten(s: str, n: int = 18) -> str:
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"

def render_sankey(sankey, title="Material Flow Map"):
    full_labels = sankey["labels"]
    short_labels = [_shorten(l, 18) for l in full_labels]

    fig = go.Figure(
        data=[
            go.Sankey(
                arrangement="snap",
                node=dict(
                    label=short_labels,
                    pad=28,
                    thickness=22,
                    line=dict(width=0.5),
                    hovertemplate="%{customdata}<extra></extra>",
                    customdata=full_labels,
                ),
                link=dict(
                    source=sankey["sources"],
                    target=sankey["targets"],
                    value=sankey["values"],
                    hovertemplate="Flow: %{value:,.0f} kg<extra></extra>",
                ),
            )
        ]
    )

    fig.update_layout(
        title=title,
        font_size=11,
        height=520,
        margin=dict(l=10, r=10, t=60, b=10),
    )
    return fig

def render_energy(results):
    st.metric("Electricity (kWh)", f"{results['energy_elec_kwh']:,.0f}")
    st.metric("Gas (kWh)", f"{results['energy_gas_kwh']:,.0f}")
    st.metric("Energy intensity (kWh/kg product)", f"{results['energy_intensity_kwh_per_kg']:.3f}")

    if results.get("energy_alloc_table") is not None:
        st.caption("Allocated energy by process (proxy-based; editable assumption)")
        st.dataframe(results["energy_alloc_table"], use_container_width=True)

def render_circularity(results):
    c1, c2 = st.columns(2)
    with c1:
        st.metric("Diverted from landfill (kg)", f"{results['diverted_kg']:,.0f}")
    with c2:
        st.metric("Diversion rate (%)", f"{results['diversion_pct']:.1f}")

    if results.get("waste_by_type") is not None and not results["waste_by_type"].empty:
        st.caption("Waste by type")
        st.dataframe(results["waste_by_type"], use_container_width=True)

    st.caption("Circular opportunities (rule-based prompts)")
    if results.get("opportunities"):
        for o in results["opportunities"]:
            st.write(f"• {o}")
    else:
        st.write("• Not enough detail to suggest opportunities yet.")
