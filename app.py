if captain_options:

        best = captain_options[0]

        st.success(
            f"### Captain: {best['player']['web_name']}"
        )

        st.write(
            f"Projected next-GW score: "
            f"{best['projection']:.1f}"
        )

        st.write(
            f"Fixture score: "
            f"{best['fixture']:.1f}/5"
        )

        if len(captain_options) > 1:

            st.subheader(
                "Captain alternatives"
            )

            captain_rows = []

            for item in captain_options[:5]:

                captain_rows.append({
                    "Player": item["player"]["web_name"],
                    "Projection": item["projection"],
                    "Fixture": round(
                        item["fixture"],
                        2
                    ),
                    "Captain Score": round(
                        item["score"],
                        2
                    )
                })

            st.dataframe(
                captain_rows,
                use_container_width=True,
                hide_index=True
            )


# ============================================================
# CHIP TAB
# ============================================================

with tab_chips:

    st.header("🎯 Chip Planner")

    st.write(
        "Chip recommendations look for fixture swings, "
        "Double Gameweeks and Blank Gameweeks."
    )

    chips = chip_analysis()

    for chip_name, data in chips.items():

        st.subheader(
            chip_name
        )

        if not data:

            st.info(
                "Not enough fixture information yet."
            )

            continue

        ranked = sorted(
            data,
            key=lambda x: x["score"],
            reverse=True
        )

        best = ranked[0]

        if best["score"] <= 0:

            st.write(
                "No strong opportunity detected yet."
            )

        else:

            st.success(
                f"Best current window: "
                f"Gameweek {best['gw']}"
            )

        rows = []

        for item in ranked[:5]:

            rows.append({
                "Gameweek": item["gw"],
                "Opportunity Score": round(
                    item["score"],
                    2
                )
            })

        st.dataframe(
            rows,
            use_container_width=True,
            hide_index=True
        )


# ============================================================
# PLAYER TAB
# ============================================================

with tab_players:

    st.header("📊 Player Rankings")

    ranking = []

    for p in players:

        if p.get("status") == "u":
            continue

        projection = adjusted_projection(
            p,
            weeks
        )

        price = (
            safe_float(
                p.get("now_cost")
            ) / 10
        )

        if price <= 0:
            continue

        ranking.append({
            "Player": p["web_name"],
            "Position": position_map.get(
                p["element_type"],
                "?"
            ),
            "Price": f"£{price:.1f}m",
            "Form": p.get("form", 0),
            "PPG": p.get("points_per_game", 0),
            "Projection": projection,
            "Value": round(
                projection / price,
                2
            )
        })

    ranking.sort(
        key=lambda x: x["Projection"],
        reverse=True
    )

    st.subheader(
        f"Best projected players - next {weeks} GWs"
    )

    st.dataframe(
        ranking[:50],
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# CHIP / TRANSFER SUMMARY
# ============================================================

st.divider()

st.subheader("🤖 Assistant Summary")

if transfer_options:

    best_transfer = transfer_options[0]

    out = best_transfer["out"]
    incoming = best_transfer["in"]

    hit = analyse_hit(
        best_transfer
    )

    st.write(
        f"Best transfer: "
        f"{out['web_name']} → "
        f"{incoming['web_name']} "
        f"(projected improvement "
        f"+{best_transfer['gain']:.1f} over the analysis window)."
    )

    if (
        allow_hits
        and hit["verdict"] == "STRONG -4"
    ):

        st.write(
            "The model considers the -4 potentially worthwhile "
            "because the projected improvement is large enough "
            "to recover the hit."
        )

    else:

        st.write(
            "The model does not currently see a strong reason "
            "to spend 4 points on the move."
        )

else:

    st.write(
        "No major transfer opportunity currently stands out."
    )

st.caption(
    "Projections are estimates, not guarantees. "
    "Use the recommendations alongside team news, injuries, "
    "rotation risk and your own judgement."
)
