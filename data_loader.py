import os

import geopandas as gpd
import pandas as pd
import streamlit as st

PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
GPKG_PATH = os.path.join(PROJ_DIR, "dados", "base_concessoes_28-08-2026.gpkg")
XLSX_PATH = os.path.join(PROJ_DIR, "dados", "base_concessoes_28-08-2026_.xlsx")
# Aproximadamente 200 m em latitude. Mantém o desenho das rodovias para a
# visualização nacional e evita enviar mais de um milhão de vértices ao navegador.
MAP_SIMPLIFICATION_TOLERANCE = 0.002

COLUMN_LABELS = {
    "vl_codigo": "Código (PNV)",
    "vl_br": "BR",
    "sg_uf": "UF",
    "ds_local_i": "Local inicial",
    "ds_local_f": "Local final",
    "vl_km_inic": "Km inicial",
    "vl_km_fina": "Km final",
    "vl_extensa": "Extensão do trecho (km)",
    "id_concessao": "ID concessão",
    "nm_fantasia": "Concessionária (fantasia)",
    "nm_popular": "Nome popular",
    "ds_trecho": "Descrição do trecho",
    "vl_extensao_km": "Extensão (km)",
    "dt_assinatura": "Data de assinatura",
    "dt_inicio": "Início da concessão",
    "dt_fim": "Fim da concessão",
    "dt_leilao": "Previsão de leilão",
    "vl_prazo_anos": "Prazo (anos)",
    "nm_empresa": "Empresa",
    "sg_uf_concessoes": "UF do contrato",
    "versao_snv": "Versão do SNV",
    "ds_fase": "Fase (original)",
    "fase_rotulo": "Fase",
    "status": "Status",
    "situacao_leilao": "Situação do leilão",
}

FASE_COLORS = {
    "Concessão": "#1f77b4",
    "Concessão – otimização": "#2ca02c",
    "Concessão – aditivo": "#17becf",
    "Concessão – termo de arrolamento": "#9467bd",
    "Processo de concessão": "#ff7f0e",
    "Processo de concessão – leiloado": "#e377c2",
    "Encerrado": "#d62728",
    "Sem informação": "#7f7f7f",
}

STATUS_COLORS = {
    "Concessão ativa": "#1f77b4",
    "Em processo": "#ff7f0e",
    "Encerrado": "#d62728",
    "Sem informação": "#7f7f7f",
}

STATUS_ORDER = ["Concessão ativa", "Em processo", "Encerrado", "Sem informação"]

LEILAO_COLORS = {
    "Leilão previsto": "#ff7f0e",
    "Leilão realizado": "#1f77b4",
    "Sem informação": "#7f7f7f",
}


@st.cache_resource(show_spinner="Carregando base de concessões...")
def load_data() -> gpd.GeoDataFrame:
    """Carrega e normaliza o GeoPackage (cache em memória)."""
    gdf = gpd.read_file(GPKG_PATH)

    date_cols = ["dt_assinatura", "dt_inicio", "dt_fim", "dt_leilao"]
    for c in date_cols:
        gdf[c] = pd.to_datetime(gdf[c], errors="coerce")

    for c in ["vl_km_inic", "vl_km_fina", "vl_extensa", "vl_extensao_km"]:
        gdf[c] = pd.to_numeric(gdf[c], errors="coerce")
    gdf["vl_prazo_anos"] = pd.to_numeric(gdf["vl_prazo_anos"], errors="coerce")

    gdf["fase_rotulo"] = gdf["ds_fase"].map(_normalize_fase)
    gdf["status"] = gdf.apply(_derive_status, axis=1)
    gdf["situacao_leilao"] = gdf["dt_leilao"].map(_derive_leilao)
    gdf["pesquisa"] = _build_search_haystack(gdf)
    # A geometria original é preservada. A versão simplificada é preparada uma
    # única vez no cache e é usada somente no mapa leve.
    gdf["geometry_mapa"] = (
        gdf.geometry.simplify(MAP_SIMPLIFICATION_TOLERANCE, preserve_topology=False)
        .set_precision(0.0001)
    )
    return gdf


@st.cache_data(show_spinner=False)
def load_dicionario() -> pd.DataFrame:
    sheet = pd.read_excel(XLSX_PATH, sheet_name="dicionario_de_dados")
    sheet.columns = [str(column).strip() for column in sheet.columns]
    return sheet


@st.cache_data(show_spinner=False)
def load_historico() -> pd.DataFrame:
    sheet = pd.read_excel(XLSX_PATH, sheet_name="historico_alteracoes")
    sheet = sheet.rename(columns={c: str(c).strip() for c in sheet.columns})
    sheet["DATA"] = pd.to_datetime(sheet["DATA"], errors="coerce")
    return sheet


def _normalize_fase(fase: object) -> str:
    """Converte a fase original (sujeita a variações) em rótulo amigável."""
    if fase is None or (isinstance(fase, float) and pd.isna(fase)):
        return "Sem informação"
    f = str(fase).casefold().replace("_", " ").replace("&", " ")
    if "encerrado" in f:
        return "Encerrado"
    if "arrolamento" in f:
        return "Concessão – termo de arrolamento"
    if "leiloado" in f:
        return "Processo de concessão – leiloado"
    if "aditivo" in f:
        return "Concessão – aditivo"
    if "otimiza" in f:
        return "Concessão – otimização"
    if "processo" in f:
        return "Processo de concessão"
    return "Concessão"


def _derive_status(row: pd.Series) -> str:
    fase = str(row["ds_fase"]).casefold() if pd.notna(row["ds_fase"]) else ""
    if "encerrado" in fase:
        return "Encerrado"
    if "processo" in fase:
        return "Em processo"
    hoje = pd.Timestamp.today().normalize()
    if pd.notna(row["dt_fim"]):
        return "Concessão ativa" if row["dt_fim"] >= hoje else "Encerrado"
    if pd.notna(row["dt_inicio"]):
        return "Concessão ativa"
    return "Sem informação"


def _derive_leilao(dt: object) -> str:
    """Classifica o trecho pela situação da data de leilão (se informada)."""
    if dt is None or pd.isna(dt):
        return "Sem informação"
    hoje = pd.Timestamp.today().normalize()
    return "Leilão realizado" if dt < hoje else "Leilão previsto"


def _build_search_haystack(gdf: gpd.GeoDataFrame) -> pd.Series:
    cols = ["ds_trecho", "ds_local_i", "ds_local_f", "vl_codigo", "nm_fantasia", "nm_empresa"]
    parts = [gdf[c].fillna("").astype(str) for c in cols if c in gdf.columns]
    return pd.concat(parts, axis=1).agg(" ".join, axis=1).str.casefold()


def apply_filters(
    df: gpd.GeoDataFrame,
    ufs=None,
    brs=None,
    fases=None,
    statuses=None,
    concessions=None,
    km_range=None,
    search_text=None,
    anos_venc=None,
    empresas=None,
) -> gpd.GeoDataFrame:
    out = df.copy()
    if ufs:
        out = out[out["sg_uf_concessoes"].isin(ufs)]
    if brs:
        brs = {str(b) for b in brs}
        out = out[out["vl_br"].astype(str).isin(brs)]
    if fases:
        out = out[out["fase_rotulo"].isin(fases)]
    if statuses:
        out = out[out["status"].isin(statuses)]
    if concessions:
        out = out[out["nm_fantasia"].isin(concessions)]
    if empresas:
        out = out[out["nm_empresa"].isin(empresas)]
    if anos_venc:
        out = out[out["dt_fim"].dt.year.isin(anos_venc)]
    if km_range is not None:
        lo, hi = km_range
        km = out["vl_extensao_km"].fillna(0.0)
        out = out[(km >= lo) & (km <= hi)]
    if search_text:
        t = search_text.strip().casefold()
        out = out[out["pesquisa"].str.contains(t, regex=False)]
    return out


def formatted_table(df: gpd.GeoDataFrame) -> pd.DataFrame:
    """Usa o dicionário como fonte única de nomes e ordenação das colunas."""
    dictionary = load_dicionario()
    cols = [
        column
        for column in dictionary["Nome da Coluna"].dropna().astype(str)
        if column in df.columns
    ]
    return df[cols].copy()
