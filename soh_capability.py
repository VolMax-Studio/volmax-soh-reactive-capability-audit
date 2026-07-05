#!/usr/bin/env python3
"""
soh_capability.py — degradation-aware reactive-power capability for BESS inverters.

Maps battery state-of-health (SOH) and internal resistance (R_int) to the SHRUNK
P-Q capability envelope a real, aged battery can actually support — versus the
ideal envelope assumed when SOH is taken at face value (or inflated by a
leakage-optimistic estimate).

Uses representative parameters for a utility-scale ~3.45 MVA BESS inverter class
(illustrative, not tied to a specific verified datasheet).

PHYSICS (instantaneous / grid-stability timescale, NOT energy-depletion):
  1. Aged cell -> higher R_int -> larger I*R_int drop under load
        V_dc = OCV(SOC) - I_dc * R_int
  2. Lower V_dc lowers the inverter's max AC voltage before overmodulation
        V_ac_max = m_max * V_dc / 2        (m_max ~1.0 linear, ~1.15 with 3rd-harm)
  3. Apparent power is bounded by BOTH the inverter current limit and S_rated:
        S_avail = min( S_rated , sqrt(3) * V_ac * I_inv_max )
     and the deliverable reactive headroom at a given P is
        Q_avail = sqrt( max(S_avail^2 - P^2, 0) )
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field, asdict
from math import sqrt


@dataclass
class InverterSpec:
    s_rated_mva: float                 # nameplate apparent power
    v_dc_nominal_v: float              # nominal DC-link voltage at healthy battery
    i_inv_max_a: float                 # inverter AC current limit (per phase, RMS)
    v_ac_nominal_v: float              # nominal AC line voltage (RMS, L-L)
    m_max: float = 1.0                 # modulation index ceiling (1.0 linear SVPWM;
                                       # up to ~1.155 with third-harmonic injection)


@dataclass
class BatteryState:
    soh_fraction: float                # 1.0 = new; e.g. 0.80 aged, or an inflated 0.95
    r_int_ohm: float                   # present internal resistance (rises with age)
    ocv_v: float                       # open-circuit DC voltage at current SOC
    label: str = ""                    # "honest" / "leakage_inflated" / etc.
    r_int_scatter: float = 0.15        # 15% uncertainty scatter of internal resistance


def reactive_capability(
    p_mw: float,
    batt: BatteryState,
    inv: InverterSpec,
    *,
    i_dc_a: float | None = None,
) -> dict:
    """Return the deliverable Q headroom (Mvar) and the shrunk apparent-power
    limit for the given operating point, under the battery's present state.
    Includes uncertainty bounds based on r_int_scatter.
    """
    p_w = p_mw * 1e6
    if i_dc_a is None:
        # estimate of DC current from active power and nominal link voltage
        i_dc_a = p_w / max(inv.v_dc_nominal_v, 1e-6)

    def _calc_q_for_r_int(r):
        v_dc = batt.ocv_v - i_dc_a * r
        if v_dc <= 0:
            return 0.0, 0.0, v_dc, 0.0
        # AC voltage ceiling set by modulation against the real V_dc
        v_ac_max = inv.m_max * v_dc / 2.0
        v_dc_ceiling_healthy = inv.m_max * inv.v_dc_nominal_v / 2.0
        modulation_headroom = min(1.0, v_ac_max / v_dc_ceiling_healthy)

        # Apparent-power limit: min(nameplate, current-limit, modulation)
        s_current_limit = sqrt(3) * inv.v_ac_nominal_v * inv.i_inv_max_a / 1e6   # MVA
        s_avail = min(inv.s_rated_mva, s_current_limit) * modulation_headroom

        # Reactive headroom at this P
        q_sq = s_avail**2 - p_mw**2
        q_avail = sqrt(q_sq) if q_sq > 0 else 0.0

        # physics gate
        if q_avail > 0:
            s_check = sqrt(p_mw**2 + q_avail**2)
            assert abs(s_check - s_avail) < 1e-6, (
                f"apparent-power identity violated: {s_check} != {s_avail}"
            )
        return q_avail, s_avail, v_dc, modulation_headroom

    # Propagate uncertainty bounds:
    # r_int_max represents worst case (lowest Q); r_int_min represents best case (highest Q)
    r_nom = batt.r_int_ohm
    r_max = r_nom * (1.0 + batt.r_int_scatter)
    r_min = r_nom * (1.0 - batt.r_int_scatter)

    q_nom, s_nom, v_dc_nom, mod_nom = _calc_q_for_r_int(r_nom)
    q_min, s_min, v_dc_min, _ = _calc_q_for_r_int(r_max)
    q_max, s_max, v_dc_max, _ = _calc_q_for_r_int(r_min)

    if v_dc_nom <= 0:
        return {
            "label": batt.label, "p_mw": p_mw, "v_dc_v": round(v_dc_nom, 1),
            "q_avail_mvar": 0.0, "s_avail_mva": 0.0,
            "note": "V_dc collapsed under load (R_int too high for this current)",
        }

    return {
        "label": batt.label,
        "p_mw": round(p_mw, 4),
        "v_dc_v": round(v_dc_nom, 1),
        "v_dc_min_v": round(v_dc_min, 1),
        "v_dc_max_v": round(v_dc_max, 1),
        "modulation_headroom": round(mod_nom, 4),
        "s_avail_mva": round(s_nom, 4),
        "s_avail_min_mva": round(s_min, 4),
        "s_avail_max_mva": round(s_max, 4),
        "q_avail_mvar": round(q_nom, 4),
        "q_avail_mvar_min": round(q_min, 4),
        "q_avail_mvar_max": round(q_max, 4),
        "p_over_s_limited": p_mw >= s_nom,
    }


def compare_honest_vs_inflated(
    p_mw: float,
    q_required_mvar: float,
    honest: BatteryState,
    inflated: BatteryState,
    inv: InverterSpec,
) -> dict:
    h = reactive_capability(p_mw, honest, inv)
    f = reactive_capability(p_mw, inflated, inv)

    def verdict(cap):
        return {
            **cap,
            "q_required_mvar": q_required_mvar,
            "meets_requirement": cap["q_avail_mvar"] >= q_required_mvar,
            "q_shortfall_mvar": round(max(0.0, q_required_mvar - cap["q_avail_mvar"]), 4),
        }

    vh, vf = verdict(h), verdict(f)
    return {
        "operating_point": {"p_mw": p_mw, "q_required_mvar": q_required_mvar},
        "honest_soh": vh,
        "inflated_soh": vf,
        "verdict_flips": vh["meets_requirement"] != vf["meets_requirement"],
        "interpretation": (
            "Inflated SOH reports compliance the real battery cannot deliver — "
            "the grid-code check passes on paper and fails in the field."
            if (vf["meets_requirement"] and not vh["meets_requirement"])
            else "Both inputs agree at this operating point; sweep P to find where they diverge."
        ),
        "assumptions": {
            "model": "single-stage R_int -> V_dc -> modulation/current-limit -> Q",
            "timescale": "instantaneous (grid-stability); SOC depletion NOT modelled",
            "out_of_scope": ["thermal derating", "inverter control-loop dynamics",
                             "SOC energy duration", "harmonic limits"],
            "inverter": asdict(inv),
            "honest_state": asdict(honest),
            "inflated_state": asdict(inflated),
            "note": "Representative parameters for a utility-scale BESS inverter class (illustrative, not a verified datasheet).",
        },
    }


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Calculate SOH-aware reactive power (Mvar) capability for BESS inverters."
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Run the comparison sweep demo comparing honest vs. inflated SOH"
    )
    parser.add_argument(
        "--p-mw", type=float, default=2.8,
        help="Active power dispatch level (MW)"
    )
    parser.add_argument(
        "--soh", type=float, default=0.80,
        help="Battery State of Health fraction (0.0 to 1.0)"
    )
    parser.add_argument(
        "--r-int", type=float, default=0.045,
        help="Battery internal resistance (Ohm)"
    )
    parser.add_argument(
        "--ocv", type=float, default=1080.0,
        help="Battery open-circuit voltage under load (V)"
    )
    parser.add_argument(
        "--s-rated", type=float, default=3.45,
        help="Inverter rated apparent power (MVA)"
    )
    parser.add_argument(
        "--v-dc-nominal", type=float, default=1100.0,
        help="Inverter nominal DC-link voltage (V)"
    )
    parser.add_argument(
        "--i-inv-max", type=float, default=3621.0,
        help="Inverter maximum AC current limit (A)"
    )
    parser.add_argument(
        "--v-ac-nominal", type=float, default=550.0,
        help="Inverter nominal AC line-to-line voltage (V)"
    )
    parser.add_argument(
        "--m-max", type=float, default=1.0,
        help="Inverter modulation index ceiling"
    )

    args = parser.parse_args()

    # If no arguments provided or explicitly requesting --demo, run the sweep demo
    if args.demo or len(sys.argv) == 1:
        inv = InverterSpec(
            s_rated_mva=3.45, v_dc_nominal_v=1100.0,
            i_inv_max_a=3621.0, v_ac_nominal_v=550.0, m_max=1.0,
        )
        honest = BatteryState(soh_fraction=0.80, r_int_ohm=0.045,
                              ocv_v=1080.0, label="honest")
        inflated = BatteryState(soh_fraction=0.95, r_int_ohm=0.025,
                                ocv_v=1095.0, label="leakage_inflated")

        print("  [note] Calibrated with representative utility-scale inverter specifications.\n")
        print("  P(MW)  Q_req   Q_honest (min/max)       Q_inflated (min/max)     flips?")
        for p in [1.0, 2.0, 2.5, 2.8, 3.0]:
            r = compare_honest_vs_inflated(p, q_required_mvar=1.5,
                                           honest=honest, inflated=inflated, inv=inv)
            q_h = f"{r['honest_soh']['q_avail_mvar']:.3f} ({r['honest_soh']['q_avail_mvar_min']:.3f}/{r['honest_soh']['q_avail_mvar_max']:.3f})"
            q_i = f"{r['inflated_soh']['q_avail_mvar']:.3f} ({r['inflated_soh']['q_avail_mvar_min']:.3f}/{r['inflated_soh']['q_avail_mvar_max']:.3f})"
            print(f"  {p:>4.1f}   1.5    {q_h:<24} {q_i:<24} {'YES' if r['verdict_flips'] else 'no'}")

        print()
        detail = compare_honest_vs_inflated(2.8, 1.5, honest, inflated, inv)
        print(json.dumps(detail, indent=2))
    else:
        inv = InverterSpec(
            s_rated_mva=args.s_rated,
            v_dc_nominal_v=args.v_dc_nominal,
            i_inv_max_a=args.i_inv_max,
            v_ac_nominal_v=args.v_ac_nominal,
            m_max=args.m_max,
        )
        batt = BatteryState(
            soh_fraction=args.soh,
            r_int_ohm=args.r_int,
            ocv_v=args.ocv,
            label="custom"
        )
        res = reactive_capability(args.p_mw, batt, inv)
        print(json.dumps(res, indent=2))
