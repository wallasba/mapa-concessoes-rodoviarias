import base64
import json
import math

import folium
import geopandas as gpd
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from data_loader import (
    COLUMN_LABELS,
    DISPLAY_COLUMNS,
    FASE_COLORS,
    LEILAO_COLORS,
    STATUS_ORDER,
    apply_filters,
    formatted_table,
    load_data,
    load_dicionario,
    load_historico,
)

st.set_page_config(
    page_title="Mapa de Concessões Rodoviárias",
    page_icon="🛣️",
    layout="wide",
    initial_sidebar_state="expanded",
)

MAP_WIDTH = 1200
MAP_HEIGHT = 660
MAP_PROPERTIES = [
    "vl_br",
    "nm_fantasia",
    "ds_trecho",
    "fase_rotulo",
    "status",
    "vl_extensao_km",
]


def map_geojson(gdf_data: gpd.GeoDataFrame) -> dict:
    """Prepara apenas os atributos necessários e a geometria simplificada."""
    properties = [column for column in MAP_PROPERTIES if column in gdf_data.columns]
    map_data = gpd.GeoDataFrame(
        gdf_data[properties].copy(),
        geometry=gdf_data["geometry_mapa"],
        crs=gdf_data.crs,
    )
    map_data["vl_extensao_km"] = pd.to_numeric(
        map_data["vl_extensao_km"], errors="coerce"
    ).round(1)
    return json.loads(map_data.to_json(na="null", drop_id=True))


def valid_map_rows(gdf_data: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Remove geometrias ausentes ou vazias antes de enviar dados ao Leaflet."""
    geometry = gdf_data["geometry_mapa"]
    return gdf_data.loc[~geometry.isna() & ~geometry.is_empty]


def build_map_html(gdf_data: gpd.GeoDataFrame) -> str:
    """Cria um Folium dinâmico sem tiles ou chamadas a APIs cartográficas."""
    m = folium.Map(
        location=[-14.5, -51.0],
        zoom_start=5,
        tiles=None,
        height=MAP_HEIGHT,
        prefer_canvas=True,
        control_scale=True,
    )
    bounds = gdf_data["geometry_mapa"].dropna().total_bounds
    if all(math.isfinite(value) for value in bounds):
        m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])

    def style_fn(feature):
        fase = feature["properties"].get("fase_rotulo") or "Sem informação"
        return {
            "color": FASE_COLORS.get(fase, "#7f7f7f"),
            "weight": 2,
            "opacity": 0.85,
        }

    tooltip_fields = [
        field for field in ("vl_br", "nm_fantasia", "ds_trecho", "fase_rotulo")
        if field in gdf_data.columns
    ]
    popup_fields = [field for field in MAP_PROPERTIES if field in gdf_data.columns]
    aliases = {field: COLUMN_LABELS.get(field, field) + ": " for field in popup_fields}
    folium.GeoJson(
        map_geojson(gdf_data),
        name="Trechos de concessão",
        style_function=style_fn,
        tooltip=folium.GeoJsonTooltip(
            fields=tooltip_fields,
            aliases=[aliases[field] for field in tooltip_fields],
            sticky=False,
        ),
        popup=folium.GeoJsonPopup(
            fields=popup_fields,
            aliases=[aliases[field] for field in popup_fields],
            localize=True,
            max_width=420,
        ),
        smooth_factor=1.5,
    ).add_to(m)
    return m.get_root().render()


def map_signature(df: pd.DataFrame) -> tuple:
    """Identifica a seleção sem serializar a geometria completa na sessão."""
    return tuple(df.index.tolist())


def map_iframe_source(map_html: str) -> str:
    """Entrega o HTML do Folium diretamente ao iframe, sem um servidor externo."""
    encoded = base64.b64encode(map_html.encode("utf-8")).decode("ascii")
    return f"data:text/html;charset=utf-8;base64,{encoded}"


def serialize_geojson_export(df: gpd.GeoDataFrame) -> bytes:
    """Serializa GeoJSON apenas quando o usuário pede o download."""
    geo_cols = ["geometry"] + [column for column in DISPLAY_COLUMNS if column in df.columns]
    geojson_export = df[geo_cols].copy()
    for column in geojson_export.select_dtypes(include=["datetime64", "datetimetz"]):
        geojson_export[column] = geojson_export[column].dt.strftime("%Y-%m-%d")
    return geojson_export.to_json(na="null").encode("utf-8")


def legend_html(items: list, colors: dict) -> str:
    badges = "".join(
        f'<span style="display:inline-flex;align-items:center;margin-right:14px;">'
        f'<span style="width:14px;height:5px;background:{colors[c]};'
        f'border-radius:2px;margin-right:6px;display:inline-block;"></span>{c}</span>'
        for c in items
    )
    return f'<div style="font-size:12px;color:#555;padding:4px 0;">{badges}</div>'


def kpis(df):
    total_km = float(df["vl_extensao_km"].sum()) if len(df) else 0.0
    ativas = len(df[df["status"] == "Concessão ativa"])
    processo = len(df[df["status"] == "Em processo"])
    concessoes = df["id_concessao"].dropna().nunique()
    st.markdown(f"**{len(df):,} segmentos** exibidos de um total de {len(load_data()):,} na base completa.", )
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Segmentos", f"{len(df):,}")
    c2.metric("Extensão total (km)", f"{total_km:,.0f}")
    c3.metric("Concessões distintas", f"{concessoes:,}")
    c4.metric("Concessão ativa", f"{ativas:,}")
    c5.metric("Em processo", f"{processo:,}")


def tab_mapa(df):
    if len(df) == 0:
        st.warning("Nenhum segmento atende aos filtros selecionados.")
        return
    map_rows = valid_map_rows(df)
    if map_rows.empty:
        st.warning("Os trechos filtrados não possuem geometria válida para exibição no mapa.")
        return
    fase_order = sys_fases(map_rows)
    st.markdown(
        f"**{len(map_rows):,} trechos** em mapa interativo. Arraste, amplie, clique em um segmento "
        f"para os atributos e passe o mouse para um resumo.",
    )
    signature = map_signature(map_rows)
    current_signature = st.session_state.get("map_signature")
    refresh = st.button("Atualizar mapa com os filtros atuais", type="primary", key="map_refresh")
    if current_signature is None or refresh:
        with st.spinner("Preparando mapa interativo..."):
            st.session_state["map_html"] = build_map_html(map_rows)
            st.session_state["map_signature"] = signature
        current_signature = signature
    if current_signature != signature:
        st.info("Os filtros foram alterados. Clique em “Atualizar mapa com os filtros atuais”.")
        return

    map_html = st.session_state["map_html"]
    st.iframe(map_iframe_source(map_html), height=MAP_HEIGHT)
    st.markdown(legend_html(fase_order, FASE_COLORS), unsafe_allow_html=True)
    leilao_present = sorted(
        {v for v in df["situacao_leilao"].dropna().unique() if v in LEILAO_COLORS},
        key=lambda v: list(LEILAO_COLORS).index(v),
    )
    if leilao_present:
        st.markdown(legend_html(leilao_present, LEILAO_COLORS), unsafe_allow_html=True)
    st.download_button(
        "⬇️ Baixar mapa interativo (HTML)",
        map_html.encode("utf-8"),
        file_name="mapa_concessoes.html",
        mime="text/html",
    )


def sys_fases(df):
    present = [f for f in df["fase_rotulo"].dropna().unique() if f in FASE_COLORS]
    ordered = [f for f in FASE_COLORS if f in present]
    ordered += [f for f in present if f not in ordered]
    return ordered


def tab_estatisticas(df):
    if len(df) == 0:
        st.warning("Nenhum segmento com os filtros atuais.")
        return
    c1, c2 = st.columns(2)
    with c1:
        uf_km = df.groupby("sg_uf")["vl_extensao_km"].sum().sort_values(ascending=False)
        fig = px.bar(
            x=uf_km.index,
            y=uf_km.values,
            labels={"x": "UF", "y": "km"},
            title="Extensão total por UF",
            color=uf_km.index,
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        st.plotly_chart(fig, width="stretch")
    with c2:
        fase_cnt = df["fase_rotulo"].value_counts()
        fig2 = go.Figure(
            go.Pie(
                labels=fase_cnt.index,
                values=fase_cnt.values,
                hole=0.45,
                marker=dict(colors=[FASE_COLORS.get(f, "#7f7f7f") for f in fase_cnt.index]),
            )
        )
        fig2.update_layout(title="Trechos por fase")
        st.plotly_chart(fig2, width="stretch")

    c3, c4 = st.columns(2)
    with c3:
        top = (
            df.groupby("nm_fantasia")["vl_extensao_km"]
            .sum()
            .sort_values(ascending=True)
            .tail(15)
        )
        fig3 = px.bar(
            x=top.values,
            y=top.index,
            orientation="h",
            labels={"x": "km", "y": ""},
            title="Top 15 concessionárias por extensão",
            color_discrete_sequence=px.colors.qualitative.Bold,
        )
        st.plotly_chart(fig3, width="stretch")
    with c4:
        anos = df.dropna(subset=["dt_inicio"])["dt_inicio"].dt.year.value_counts().sort_index()
        st.plotly_chart(
            px.bar(
                x=anos.index,
                y=anos.values,
                labels={"x": "Ano", "y": "Trechos"},
                title="Início de contratos por ano",
                color_discrete_sequence=["#2ca02c"],
            ),
            width="stretch",
        )

    c5, c6 = st.columns(2)
    with c5:
        br_km = (
            df.groupby("vl_br")["vl_extensao_km"].sum().sort_values(ascending=False).head(12)
        )
        fig5 = px.bar(
            x=br_km.index,
            y=br_km.values,
            labels={"x": "BR", "y": "km"},
            title="Top 12 rodovias (BR) por extensão",
        )
        st.plotly_chart(fig5, width="stretch")
    with c6:
        status_km = (
            df.groupby("status")["vl_extensao_km"]
            .sum()
            .reindex([s for s in STATUS_ORDER if s in df["status"].unique()])
        )
        st.plotly_chart(
            px.bar(
                x=status_km.index,
                y=status_km.values,
                labels={"x": "Status", "y": "km"},
                title="Extensão por status",
                color=status_km.index,
                color_discrete_map={
                    "Concessão ativa": "#1f77b4",
                    "Em processo": "#ff7f0e",
                    "Encerrado": "#d62728",
                    "Sem informação": "#7f7f7f",
                },
            ),
            width="stretch",
        )


def tab_dados(df):
    if len(df) == 0:
        st.warning("Nenhum segmento com os filtros atuais.")
        return
    tbl = formatted_table(df)
    st.dataframe(tbl.reset_index(drop=True), width="stretch", height=520)
    c1, c2 = st.columns(2)
    c1.download_button(
        "⬇️ Baixar dados filtrados (CSV)",
        tbl.to_csv(index=False).encode("utf-8-sig"),
        file_name="concessoes_filtradas.csv",
        mime="text/csv",
    )
    signature = map_signature(df)
    is_current = st.session_state.get("geojson_signature") == signature
    if c2.button("Preparar GeoJSON para download", key="prepare_geojson"):
        with st.spinner("Gerando arquivo GeoJSON..."):
            st.session_state["geojson_download"] = serialize_geojson_export(df)
            st.session_state["geojson_signature"] = signature
        is_current = True
    if is_current:
        c2.download_button(
            "⬇️ Baixar trechos filtrados (GeoJSON)",
            st.session_state["geojson_download"],
            file_name="concessoes_filtradas.geojson",
            mime="application/geo+json",
        )
    else:
        c2.caption("A geração do GeoJSON é feita somente quando solicitada.")


def tab_dicionario():
    dic = load_dicionario()
    st.markdown("Dicionário de dados da base de concessões rodoviárias.")
    st.dataframe(dic, width="stretch", hide_index=True)


def tab_historico():
    hist = load_historico()
    st.markdown("Histórico de alterações recentes na base.")
    for _, r in hist.iterrows():
        data = r["DATA"].strftime("%d/%m/%Y") if pd.notna(r["DATA"]) else str(r["DATA"])
        with st.container(border=True):
            st.markdown(f"##### ID {r['ID']} — {data}")
            st.write(r["ALTERAÇÕES"])


def sidebar_filters(df):
    st.sidebar.header("🛣️ Filtros")
    with st.sidebar.expander("Localização", expanded=True):
        ufs = st.multiselect(
            "Unidades da Federação",
            sorted(df["sg_uf"].dropna().unique()),
            placeholder="Todas as UFs",
            key="f_uf",
        )
        brs = st.multiselect(
            "Rodovias (BR)",
            sorted({str(x) for x in df["vl_br"].dropna().unique()}),
            placeholder="Todas as BRs",
            key="f_br",
        )
    with st.sidebar.expander("Situação", expanded=True):
        fases = st.multiselect(
            "Fase",
            [f for f in FASE_COLORS if f in set(df["fase_rotulo"].unique())],
            placeholder="Todas as fases",
            key="f_fase",
        )
        statuses = st.multiselect(
            "Status",
            [s for s in STATUS_ORDER if s in set(df["status"].unique())],
            placeholder="Todos os status",
            key="f_status",
        )
    with st.sidebar.expander("Concessão", expanded=False):
        concessions = st.multiselect(
            "Concessionária",
            sorted(df["nm_fantasia"].dropna().unique()),
            placeholder="Todas",
            key="f_concessao",
        )
        empresas = st.multiselect(
            "Empresa (razão social)",
            sorted(df["nm_empresa"].dropna().unique()),
            placeholder="Todas",
            key="f_empresa",
        )
    with st.sidebar.expander("Vencimento", expanded=False):
        anos = st.multiselect(
            "Ano de vencimento (dt_fim)",
            sorted(df["dt_fim"].dropna().dt.year.astype(int).unique().tolist()),
            placeholder="Todos",
            key="f_ano",
        )
    max_km = int(df["vl_extensao_km"].fillna(0).max()) or 1
    km_range = st.sidebar.slider(
        "Extensão do trecho (km)", 0, max_km, (0, max_km), step=10, key="f_km"
    )
    search = st.sidebar.text_input("🔎 Buscar (BR, trecho, local, código)", key="f_search")
    if st.sidebar.button("Limpar filtros", width="stretch"):
        for key in [
            "f_uf",
            "f_br",
            "f_fase",
            "f_status",
            "f_concessao",
            "f_empresa",
            "f_ano",
            "f_km",
            "f_search",
        ]:
            st.session_state.pop(key, None)
        st.rerun()
    return ufs, brs, fases, statuses, concessions, empresas, anos, km_range, search


def main():
    data = load_data()

    st.title("🛣️ Mapa de Concessões Rodoviárias Federais")
    st.caption("Base do SNV — concessionárias de rodovias federais no Brasil.")

    ufs, brs, fases, statuses, concessions, empresas, anos, km_range, search = sidebar_filters(data)

    filtered = apply_filters(
        data,
        ufs,
        brs,
        fases,
        statuses,
        concessions,
        km_range,
        search.strip() or None,
        anos_venc=anos,
        empresas=empresas,
    )

    kpis(filtered)

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        ["🗺️ Mapa", "📊 Estatísticas", "📋 Dados", "📖 Dicionário de Dados", "🕓 Histórico de Alterações"]
    )

    with tab1:
        tab_mapa(filtered)
    with tab2:
        tab_estatisticas(filtered)
    with tab3:
        tab_dados(filtered)
    with tab4:
        tab_dicionario()
    with tab5:
        tab_historico()


if __name__ == "__main__":
    main()
