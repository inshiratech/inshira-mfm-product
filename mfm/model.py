import pandas as pd
import numpy as np

def build_flow_model(site_name, boundary_start, boundary_end, process_blocks, data_bundle, time_period,
                     scenarios, unit_mass_kg_per_unit=7.0, carbon_factors=None):
    return {
        "site_name": site_name,
        "boundary_start": boundary_start,
        "boundary_end": boundary_end,
        "blocks": process_blocks,
        "data": data_bundle,
        "time_period": time_period,
        "scenarios": scenarios,
        "unit_mass_kg_per_unit": float(unit_mass_kg_per_unit),
        "carbon_factors": carbon_factors or {},
    }

def _find_col(df, keywords):
    for c in df.columns:
        lc = c.lower()
        if any(k in lc for k in keywords):
            return c
    return None

def _sum_material_in_kg(material_df):
    kg_col = _find_col(material_df, ["kg", "weight"])
    return float(material_df[kg_col].astype(float).sum()) if kg_col else None

def _sum_waste_kg(waste_df):
    kg_col = _find_col(waste_df, ["kg", "quantity"])
    return float(waste_df[kg_col].astype(float).sum()) if kg_col else None

def _waste_by_type(waste_df):
    type_col = _find_col(waste_df, ["waste"])
    kg_col = _find_col(waste_df, ["kg", "quantity"])
    if not type_col or not kg_col:
        return pd.DataFrame(columns=["Waste Type", "Quantity (kg)"])
    out = waste_df[[type_col, kg_col]].copy()
    out.columns = ["Waste Type", "Quantity (kg)"]
    out["Quantity (kg)"] = out["Quantity (kg)"].astype(float)
    return out

def _energy_totals(energy_df):
    elec_col = _find_col(energy_df, ["electric"])
    gas_col  = _find_col(energy_df, ["gas"])
    elec = float(energy_df[elec_col].sum()) if elec_col else 0.0
    gas  = float(energy_df[gas_col].sum())  if gas_col else 0.0
    return elec, gas, bool(elec_col or gas_col)

def _waste_emissions_kgco2e(waste_df, factors):
    route_col = _find_col(waste_df, ["route", "disposal"])
    kg_col = _find_col(waste_df, ["kg", "quantity"])
    if not route_col or not kg_col:
        return 0.0, {}

    tmp = waste_df.copy()
    tmp[kg_col] = tmp[kg_col].astype(float)
    routes = tmp[route_col].astype(str).str.lower().str.strip()

    def route_factor(r: str) -> float:
        if "landfill" in r:
            return float(factors.get("waste_landfill_kgco2e_per_kg", 0.50))
        if "incin" in r:
            return float(factors.get("waste_incineration_kgco2e_per_kg", 0.70))
        if "recycl" in r or "recycle" in r:
            return float(factors.get("waste_recycling_kgco2e_per_kg", 0.05))
        if "haz" in r:
            return float(factors.get("waste_hazardous_kgco2e_per_kg", 1.20))
        return float(factors.get("waste_landfill_kgco2e_per_kg", 0.50))

    tmp["_ef"] = routes.apply(route_factor)
    tmp["_co2e"] = tmp[kg_col] * tmp["_ef"]
    breakdown = tmp.groupby(routes)["_co2e"].sum().to_dict()
    return float(tmp["_co2e"].sum()), breakdown

def compute_bottlenecks(blocks, total_units_required):
    rows = []
    for b in blocks:
        cap = float(b.get("capacity_units_per_hr", 0.0))
        hrs = float(b.get("available_hours", 0.0))
        down = float(b.get("downtime_pct", 0.0)) / 100.0

        eff_hrs = hrs * (1.0 - down)
        eff_cap = cap * eff_hrs

        util = (total_units_required / eff_cap) if eff_cap > 0 else np.nan

        rows.append({
            "Process": b.get("user_label", b.get("name", "Process")),
            "Capacity (units/hr)": cap,
            "Available hours": hrs,
            "Downtime %": down * 100.0,
            "Effective capacity (units/period)": eff_cap,
            "Required (units/period)": float(total_units_required),
            "Utilisation": util,
        })

    df = pd.DataFrame(rows)
    df = df.sort_values("Utilisation", ascending=False, na_position="last")

    def risk(u):
        if pd.isna(u): return "Missing data"
        if u >= 1.0: return "Bottleneck (over capacity)"
        if u >= 0.95: return "Likely bottleneck"
        if u >= 0.85: return "At risk"
        return "OK"

    df["Risk"] = df["Utilisation"].apply(risk)
    return df

def compute_balances(model):
    data = model["data"]
    blocks = model["blocks"]
    sc = model["scenarios"]

    ai_messages = []
    assumptions = []

    # --- Inputs ---
    mat_in = _sum_material_in_kg(data["material_purchases"])
    if mat_in is None:
        mat_in = 0.0
        assumptions.append("Material input mass missing; treated as 0 kg.")

    waste_df = data["waste_summary"]
    waste_out = _sum_waste_kg(waste_df)
    if waste_out is None:
        waste_out = 0.0
        assumptions.append("Waste mass missing; treated as 0 kg.")

    # Production
    prod_df = data["production_output"]
    qty_col = _find_col(prod_df, ["qty", "produced", "quantity"])
    qty = float(prod_df[qty_col].sum()) if qty_col else 0.0

    unit_mass = float(model.get("unit_mass_kg_per_unit", 7.0))
    prod_mass_out_base = qty * unit_mass
    assumptions.append(f"Converted output to mass using unit mass = {unit_mass:.2f} kg/unit.")

    # --- Scenarios ---
    scrap_reduction_pct = sc.get("scrap_reduction_pct", 0.0) / 100.0
    yield_improve_pct = sc.get("yield_improve_pct", 0.0) / 100.0
    energy_improve_pct = sc.get("energy_intensity_improve_pct", 0.0) / 100.0

    waste_out_scn = waste_out * (1.0 - scrap_reduction_pct)
    prod_mass_out = prod_mass_out_base * (1.0 + yield_improve_pct)

    if scrap_reduction_pct > 0:
        ai_messages.append(f"Scenario applied: waste reduced by {sc.get('scrap_reduction_pct',0.0):.0f}%.")
    if yield_improve_pct > 0:
        ai_messages.append(f"Scenario applied: yield improved by {sc.get('yield_improve_pct',0.0):.0f}% (proxy).")

    # --- Energy ---
    energy_df = data["energy_site"]
    elec_kwh, gas_kwh, has_energy = _energy_totals(energy_df)
    orig_elec, orig_gas = elec_kwh, gas_kwh

    if has_energy and energy_improve_pct > 0:
        elec_kwh *= (1.0 - energy_improve_pct)
        gas_kwh  *= (1.0 - energy_improve_pct)
        ai_messages.append(f"Scenario applied: energy intensity improved by {sc.get('energy_intensity_improve_pct',0.0):.0f}%.")

    allocate_energy = sc.get("allocate_energy", False)
    energy_alloc = None
    if has_energy:
        assumptions.append("Energy is site-level; process allocation is optional and uses a simple proxy.")
        if allocate_energy:
            # proxy: equal weights (simple + stable), unless you want to use yields
            weights = np.ones(len(blocks), dtype=float)
            weights = weights / weights.sum() if weights.sum() > 0 else weights
            energy_alloc = pd.DataFrame({
                "Process": [b["user_label"] for b in blocks],
                "Electricity_kWh": (elec_kwh * weights).round(0).astype(int),
                "Gas_kWh": (gas_kwh * weights).round(0).astype(int),
            })
            ai_messages.append("AI assist: allocated site energy to processes using a simple proxy (editable assumption).")

    # --- Mass balance sanity ---
    if mat_in > 0 and prod_mass_out > mat_in * 1.02:
        ai_messages.append(
            "Sanity check: product mass exceeds material input. "
            "Check 'kg per unit' (pcs→kg conversion) or material input data."
        )

    unaccounted = max(mat_in - prod_mass_out - waste_out_scn, 0.0)

    if unaccounted > 0:
        ai_messages.append(f"Detected ~{unaccounted:,.0f} kg unaccounted material (likely offcuts/rejects).")
        assumptions.append("Unaccounted mass treated as process loss (demo).")

    # --- Circularity ---
    waste_by_type = _waste_by_type(waste_df)

    route_col = _find_col(waste_df, ["route", "disposal"])
    diverted_kg = 0.0
    if route_col:
        kg_col = _find_col(waste_df, ["kg", "quantity"])
        tmp = waste_df.copy()
        tmp[kg_col] = tmp[kg_col].astype(float)
        r = tmp[route_col].astype(str).str.lower().str.strip()
        diverted_kg = float(tmp[r.str.contains("recycl|reuse|recycle", regex=True)][kg_col].sum())
    else:
        assumptions.append("No disposal route column detected; diversion % may be incomplete.")

    diverted_kg_scn = diverted_kg * (1.0 - scrap_reduction_pct)
    diversion_pct = (diverted_kg_scn / waste_out_scn * 100.0) if waste_out_scn > 0 else 0.0

    opportunities = []
    if not waste_by_type.empty:
        steel = waste_by_type[waste_by_type["Waste Type"].astype(str).str.lower().str.contains("steel|metal|scrap", regex=True)]
        if not steel.empty and float(steel["Quantity (kg)"].sum()) > 500:
            opportunities.append("High clean metal scrap: consider closed-loop recycling with supplier or local reprocessor.")
        mixed = waste_by_type[waste_by_type["Waste Type"].astype(str).str.lower().str.contains("mixed", regex=True)]
        if not mixed.empty and float(mixed["Quantity (kg)"].sum()) > 300:
            opportunities.append("Mixed waste is significant: segregation could increase recycling rate and reduce disposal cost.")
        sludge = waste_by_type[waste_by_type["Waste Type"].astype(str).str.lower().str.contains("sludge|haz", regex=True)]
        if not sludge.empty and float(sludge["Quantity (kg)"].sum()) > 100:
            opportunities.append("Hazardous/sludge stream: review upstream controls and chemical use to reduce generation.")

    # --- Carbon ---
    factors = model.get("carbon_factors", {})
    ef_e = float(factors.get("electricity_kgco2e_per_kwh", 0.20))
    ef_g = float(factors.get("gas_kgco2e_per_kwh", 0.18))

    co2e_energy = (elec_kwh * ef_e) + (gas_kwh * ef_g)
    co2e_waste, co2e_waste_breakdown = _waste_emissions_kgco2e(waste_df, factors)
    # Apply scrap reduction to waste CO2e (simple proportional MVP)
    co2e_waste_scn = co2e_waste * (1.0 - scrap_reduction_pct)
    co2e_total = co2e_energy + co2e_waste_scn

    avoided_energy_co2e = ((orig_elec - elec_kwh) * ef_e) + ((orig_gas - gas_kwh) * ef_g)
    avoided_waste_co2e = co2e_waste * scrap_reduction_pct
    co2e_avoided = max(avoided_energy_co2e + avoided_waste_co2e, 0.0)

    if co2e_avoided > 0:
        ai_messages.append(f"Estimated CO₂e avoided (scenario): ~{co2e_avoided:,.0f} kgCO₂e.")

    # --- Bottlenecks ---
    bottlenecks = compute_bottlenecks(blocks, total_units_required=qty)

    # --- Flows for Sankey ---
    rows = []
    start = model["boundary_start"]
    end = model["boundary_end"]

    # Input to first block
    rows.append({"from": start, "to": blocks[0]["user_label"], "kg": mat_in, "kind": "material_in"})

    # A simple yield cascade through blocks (better-looking Sankey)
    stage_mass = mat_in
    for i in range(len(blocks) - 1):
        y = float(blocks[i].get("yield_pct", 92)) / 100.0
        next_mass = stage_mass * y
        rows.append({"from": blocks[i]["user_label"], "to": blocks[i+1]["user_label"], "kg": max(next_mass, 0.0), "kind": "throughput"})

        loss_here = max(stage_mass - next_mass, 0.0)
        if loss_here > 0:
            rows.append({"from": blocks[i]["user_label"], "to": f"{blocks[i]['user_label']} losses", "kg": loss_here, "kind": "stage_loss"})
        stage_mass = next_mass

    # Outputs
    rows.append({"from": blocks[-1]["user_label"], "to": end, "kg": prod_mass_out, "kind": "product_out"})
    rows.append({"from": "All processes", "to": "Waste streams", "kg": waste_out_scn, "kind": "waste_out"})
    if unaccounted > 0:
        rows.append({"from": "All processes", "to": "Unaccounted losses", "kg": unaccounted, "kind": "loss_unaccounted"})

    flows_table = pd.DataFrame(rows)

    # KPIs
    material_eff = (prod_mass_out / mat_in) * 100.0 if mat_in > 0 else 0.0
    waste_intensity = (waste_out_scn / prod_mass_out) if prod_mass_out > 0 else 0.0
    energy_intensity = ((elec_kwh + gas_kwh) / prod_mass_out) if prod_mass_out > 0 else 0.0

    return {
        "mat_in_kg": mat_in,
        "prod_out_kg": prod_mass_out,
        "waste_out_kg": waste_out_scn,
        "unaccounted_kg": unaccounted,
        "material_eff_pct": material_eff,
        "waste_intensity": waste_intensity,

        "energy_elec_kwh": elec_kwh,
        "energy_gas_kwh": gas_kwh,
        "energy_intensity_kwh_per_kg": energy_intensity,
        "energy_alloc_table": energy_alloc,

        "waste_by_type": waste_by_type,
        "diversion_pct": diversion_pct,
        "diverted_kg": diverted_kg_scn,
        "opportunities": opportunities,

        "co2e_energy_kg": co2e_energy,
        "co2e_waste_kg": co2e_waste_scn,
        "co2e_total_kg": co2e_total,
        "co2e_avoided_kg": co2e_avoided,
        "co2e_waste_breakdown": co2e_waste_breakdown,

        "bottlenecks_table": bottlenecks,

        "ai_messages": ai_messages,
        "assumptions": assumptions,
        "flows_table": flows_table,
        "blocks": blocks,
        "boundary_start": start,
        "boundary_end": end,
    }

def build_sankey_inputs(results):
    flows = results["flows_table"].copy()
    labels = pd.unique(pd.concat([flows["from"], flows["to"]], ignore_index=True)).tolist()
    idx = {lab: i for i, lab in enumerate(labels)}

    sources = [idx[x] for x in flows["from"]]
    targets = [idx[x] for x in flows["to"]]
    values = flows["kg"].astype(float).tolist()

    return {"labels": labels, "sources": sources, "targets": targets, "values": values}
