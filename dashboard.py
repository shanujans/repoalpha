"""
dashboard.py — Enterprise War Room
"""

import os, json, io
from datetime import datetime
import uuid
import os, streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

if "SPEECHMATICS_API_KEY" in st.secrets:
    os.environ["SPEECHMATICS_API_KEY"] = st.secrets["SPEECHMATICS_API_KEY"]

st.set_page_config(
    page_title="RepoAlpha War Room",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;600;700&family=Space+Grotesk:wght@300;400;600;700&display=swap');
html,body,[class*="css"]{font-family:'Space Grotesk',sans-serif;background:#0E1117!important;color:#E2E8F0!important;}
.ticker-wrapper{background:#0A0D12;border-top:1px solid #1E293B;border-bottom:1px solid #00FFAA33;padding:8px 0;overflow:hidden;white-space:nowrap;}
.ticker-content{display:inline-block;animation:ticker-scroll 60s linear infinite;font-family:'JetBrains Mono',monospace;font-size:12px;color:#00FFAA;letter-spacing:.5px;}
.ticker-content:hover{animation-play-state:paused;}
@keyframes ticker-scroll{0%{transform:translateX(100vw)}100%{transform:translateX(-100%)}}
.ra-title{font-size:32px;font-weight:700;background:linear-gradient(135deg,#00FFAA,#00D4FF);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;line-height:1;}
.ra-sub{font-family:'JetBrains Mono',monospace;font-size:11px;color:#475569;letter-spacing:2px;text-transform:uppercase;}
.rating-buy{background:#00FFAA22;color:#00FFAA;border:1px solid #00FFAA44;padding:3px 10px;border-radius:4px;font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;letter-spacing:1px;}
.rating-hold{background:#FFD70022;color:#FFD700;border:1px solid #FFD70044;padding:3px 10px;border-radius:4px;font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;letter-spacing:1px;}
.rating-sell{background:#FF333322;color:#FF3333;border:1px solid #FF333344;padding:3px 10px;border-radius:4px;font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;letter-spacing:1px;}
.badge-green{background:#00FFAA15;color:#00FFAA;border:1px solid #00FFAA33;padding:2px 8px;border-radius:3px;font-size:11px;font-family:'JetBrains Mono',monospace;}
.badge-yellow{background:#FFD70015;color:#FFD700;border:1px solid #FFD70033;padding:2px 8px;border-radius:3px;font-size:11px;font-family:'JetBrains Mono',monospace;}
.badge-red{background:#FF333315;color:#FF3333;border:1px solid #FF333333;padding:2px 8px;border-radius:3px;font-size:11px;font-family:'JetBrains Mono',monospace;}
.mono{font-family:'JetBrains Mono',monospace;}
.label{font-family:'JetBrains Mono',monospace;font-size:10px;color:#334155;letter-spacing:2px;text-transform:uppercase;}
[data-testid="stMetricValue"]{font-family:'JetBrains Mono',monospace;font-size:26px!important;color:#00FFAA!important;}
[data-testid="stMetricLabel"]{color:#475569!important;font-size:10px!important;letter-spacing:1px;}
[data-testid="stSidebar"]{background:#0A0D12!important;border-right:1px solid #1E293B!important;}
.stButton button{background:transparent;border:1px solid #00FFAA44;color:#00FFAA;font-family:'JetBrains Mono',monospace;font-size:11px;letter-spacing:1px;transition:all .2s;}
.stButton button:hover{background:#00FFAA15;border-color:#00FFAA;}
[data-testid="stExpander"]{background:#0A0D12!important;border:1px solid #1E293B!important;border-radius:6px!important;}
#MainMenu,footer,.stDeployButton{visibility:hidden;}
hr{border-color:#1E293B!important;margin:14px 0!important;}
::-webkit-scrollbar{width:4px;}::-webkit-scrollbar-track{background:#0A0D12;}
::-webkit-scrollbar-thumb{background:#1E293B;border-radius:2px;}
</style>
""", unsafe_allow_html=True)


# ── Supabase ─────────────────────────────────────────────────────────────────
@st.cache_resource(ttl=0)
def get_db() -> Client:
    url = st.secrets.get("SUPABASE_URL") or os.environ["SUPABASE_URL"]
    key = st.secrets.get("SUPABASE_KEY") or os.environ["SUPABASE_KEY"]
    return create_client(url, key)

db = get_db()

if "user_id" not in st.session_state:
    st.session_state.user_id = str(uuid.uuid4())


# ── Data Loaders ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_repos(limit=50, category="All", min_score=0, rating_f="All") -> pd.DataFrame:
    q = (db.table("repositories").select("*")
         .gte("corporate_score", min_score)
         .order("corporate_score", desc=True).limit(limit))
    if category != "All": q = q.eq("market_category", category)
    if rating_f   != "All": q = q.eq("rating", rating_f)
    df = pd.DataFrame(q.execute().data)
    if df.empty: return df
    df["hype_display"]  = df.get("ai_hype_score",  pd.Series(dtype=int)).fillna(0).astype(int)
    df["license_color"] = df.get("license_color",  pd.Series(dtype=str)).fillna("yellow")
    df["rating"]        = df.get("rating",          pd.Series(dtype=str)).fillna("SELL")
    return df

@st.cache_data(ttl=300)
def load_signals(limit=300) -> pd.DataFrame:
    r = db.table("corporate_signals").select("*").order("signal_score", desc=True).limit(limit).execute()
    return pd.DataFrame(r.data)

@st.cache_data(ttl=60)
def load_stats() -> dict:
    repos    = db.table("repositories").select("id", count="exact").execute().count or 0
    signals  = db.table("corporate_signals").select("id", count="exact").execute().count or 0
    enriched = db.table("stargazers").select("id", count="exact").eq("enriched", True).execute().count or 0
    total_sg = db.table("stargazers").select("id", count="exact").execute().count or 0
    alerts   = db.table("alert_log").select("id", count="exact").execute().count or 0
    return dict(repos=repos, signals=signals, enriched=enriched, stargazers=total_sg, alerts=alerts)

@st.cache_data(ttl=60)
def load_pipeline_runs(limit=15) -> pd.DataFrame:
    r = db.table("pipeline_runs").select("*").order("started_at", desc=True).limit(limit).execute()
    return pd.DataFrame(r.data)

@st.cache_data(ttl=300)
def load_score_history(repo_id: str) -> pd.DataFrame:
    r = (db.table("score_history")
         .select("corporate_score, ai_hype_score, recorded_at")
         .eq("repo_id", repo_id).order("recorded_at").limit(100).execute())
    return pd.DataFrame(r.data)

@st.cache_data(ttl=120)
def load_watchlist(uid: str) -> list[str]:
    r = db.table("watchlist").select("repo_full_name").eq("user_id", uid).execute()
    return [row["repo_full_name"] for row in r.data]

@st.cache_data(ttl=300)
def load_alert_log(limit=25) -> pd.DataFrame:
    r = db.table("alert_log").select("*").order("alerted_at", desc=True).limit(limit).execute()
    return pd.DataFrame(r.data)


# ── Helpers ───────────────────────────────────────────────────────────────────
def toggle_watch(full_name: str, uid: str, watching: bool):
    if watching:
        db.table("watchlist").delete().eq("user_id", uid).eq("repo_full_name", full_name).execute()
    else:
        db.table("watchlist").upsert(
            {"user_id": uid, "repo_full_name": full_name, "alert_threshold": 30},
            on_conflict="user_id,repo_full_name").execute()
    st.cache_data.clear()

def export_csv(df: pd.DataFrame) -> bytes:
    cols = [c for c in ["full_name","description","language","stars_count",
                         "corporate_score","ai_hype_score","rating",
                         "license_label","market_category","tech_vibe",
                         "commercial_summary","url"] if c in df.columns]
    return df[cols].to_csv(index=False).encode()

# ── Charts ────────────────────────────────────────────────────────────────────
def gauge_chart(val: int, title: str, mx: int = 10) -> go.Figure:
    pct = val / mx
    c = "#00FFAA" if pct >= .7 else "#FFD700" if pct >= .4 else "#FF3333"
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=val,
        title={"text": title, "font": {"size": 10, "color": "#475569", "family": "JetBrains Mono"}},
        number={"font": {"size": 24, "color": c, "family": "JetBrains Mono"}},
        gauge={"axis": {"range": [0, mx], "tickwidth": 0, "tickcolor": "rgba(0,0,0,0)",
                        "tickfont": {"color": "#334155", "size": 9}, "showticklabels": False},
               "bar": {"color": c, "thickness": .6}, "bgcolor": "#0A0D12", "borderwidth": 0,
               "steps": [{"range": [0,4], "color": "rgba(255,51,51,0.09)"},
                         {"range": [4,7], "color": "rgba(255,215,0,0.09)"},
                         {"range": [7,10], "color": "rgba(0,255,170,0.09)"}]}))
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      height=140, margin={"t": 30, "b": 0, "l": 10, "r": 10})
    return fig

def history_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["recorded_at"], y=df["corporate_score"],
        name="Corp. Score", line=dict(color="#00FFAA", width=2),
        fill="tozeroy", fillcolor="rgba(0,255,170,0.05)"))
    fig.add_trace(go.Scatter(x=df["recorded_at"], y=df["ai_hype_score"],
        name="Hype", line=dict(color="#FFD700", width=1.5, dash="dot"), yaxis="y2"))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        height=200, margin={"t": 8, "b": 30, "l": 40, "r": 40},
        xaxis={"color": "#334155", "showgrid": False,
               "tickfont": {"size": 10, "family": "JetBrains Mono"}},
        yaxis={"color": "#334155", "showgrid": True, "gridcolor": "#1E293B",
               "tickfont": {"size": 10, "family": "JetBrains Mono"}},
        yaxis2={"overlaying": "y", "side": "right", "range": [0, 10],
                "color": "#334155", "showgrid": False,
                "tickfont": {"size": 10, "family": "JetBrains Mono"}},
        legend={"font": {"size": 10, "color": "#64748B", "family": "JetBrains Mono"},
                "bgcolor": "rgba(0,0,0,0)"},
        font={"family": "JetBrains Mono"})
    return fig

def treemap_chart(sig: pd.DataFrame) -> go.Figure:
    if sig.empty: return go.Figure()
    agg = (sig.groupby("company")["signal_score"].sum().reset_index()
           .pipe(lambda d: d[d["company"].notna() & (d["company"] != "")])
           .sort_values("signal_score", ascending=False).head(20))
    fig = px.treemap(agg, path=["company"], values="signal_score",
                     color="signal_score",
                     color_continuous_scale=[[0,"#1E293B"],[.5,"#0EA5E9"],[1,"#00FFAA"]])
    fig.update_traces(textfont={"family": "JetBrains Mono", "size": 12}, marker_line_width=0)
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", height=280,
                      margin={"t":0,"b":0,"l":0,"r":0}, coloraxis_showscale=False)
    return fig

def score_velocity_chart(repos_df: pd.DataFrame) -> go.Figure:
    """Bar chart of top 10 repos by corporate score."""
    if repos_df.empty: return go.Figure()
    top = repos_df.nlargest(10, "corporate_score")
    colors = ["#00FFAA" if r == "BUY" else "#FFD700" if r == "HOLD" else "#FF3333"
              for r in top["rating"]]
    fig = go.Figure(go.Bar(
        x=top["corporate_score"], y=top["full_name"].str.split("/").str[-1],
        orientation="h", marker_color=colors, marker_line_width=0))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        height=260, margin={"t": 8, "b": 8, "l": 8, "r": 8},
        xaxis={"color": "#334155", "showgrid": True, "gridcolor": "#1E293B",
               "tickfont": {"size": 10, "family": "JetBrains Mono"}},
        yaxis={"color": "#94A3B8", "showgrid": False,
               "tickfont": {"size": 10, "family": "JetBrains Mono"}},
        showlegend=False)
    return fig


# ── Ticker ────────────────────────────────────────────────────────────────────
def render_ticker(df: pd.DataFrame):
    if df.empty: return
    sym = {"BUY": "▲", "HOLD": "■", "SELL": "▼"}
    col = {"BUY": "#00FFAA", "HOLD": "#FFD700", "SELL": "#FF3333"}
    items = []
    for _, r in df.head(15).iterrows():
        rt = r.get("rating", "HOLD")
        items.append(
            f'<span style="color:#334155;margin:0 18px">◆</span>'
            f'<strong>{r.get("full_name","")}</strong> '
            f'<span style="color:{col.get(rt,"#888")}">{sym.get(rt,"■")}'
            f'{r.get("corporate_score",0)}</span> '
            f'<span style="color:#64748B">H:{int(r.get("hype_display",0))}/10</span>')
    st.markdown(
        f'<div class="ticker-wrapper"><div class="ticker-content">'
        f'📡 REPOALPHA &nbsp;&nbsp;{"".join(items)*2}</div></div>',
        unsafe_allow_html=True)


# ── Signal Card ───────────────────────────────────────────────────────────────
def render_card(row: pd.Series, sig: pd.DataFrame, watchlist: list[str]):
    full_name   = row.get("full_name", "")
    rating      = row.get("rating", "HOLD")
    score       = int(row.get("corporate_score", 0))
    hype        = int(row.get("hype_display", 0))
    desc        = (row.get("description") or "")[:110]
    summary     = row.get("commercial_summary") or ""
    vibe        = row.get("tech_vibe") or ""
    cat         = row.get("market_category") or ""
    lic_label   = row.get("license_label") or "Unknown"
    lic_color   = row.get("license_color") or "yellow"
    url         = row.get("url") or f"https://github.com/{full_name}"
    stars       = int(row.get("stars_count", 0))
    repo_id     = row.get("id")
    watching    = full_name in watchlist

    rating_html = {
        "BUY":  '<span class="rating-buy">BUY</span>',
        "HOLD": '<span class="rating-hold">HOLD</span>',
        "SELL": '<span class="rating-sell">SELL</span>',
    }.get(rating, "")
    lic_html = f'<span class="badge-{lic_color}">{lic_label}</span>'

    # Header row
    h1, h2, h3, h4 = st.columns([4, 1.2, 1.8, 0.8])
    with h1:
        st.markdown(
            f'<div class="mono" style="font-size:15px;font-weight:600;">'
            f'<a href="{url}" target="_blank" style="color:#F1F5F9;text-decoration:none;">'
            f'📦 {full_name}</a></div>'
            f'<div style="font-size:12px;color:#64748B;margin:4px 0 0">{desc}{"..." if len(row.get("description") or "")>110 else ""}</div>',
            unsafe_allow_html=True)
    with h2:
        st.markdown(f'<div style="margin-top:8px">{rating_html}</div>', unsafe_allow_html=True)
    with h3:
        st.markdown(f'<div style="margin-top:8px">{lic_html}</div>', unsafe_allow_html=True)
    with h4:
        lbl = "★ Unwatch" if watching else "☆ Watch"
        if st.button(lbl, key=f"w_{full_name}"):
            toggle_watch(full_name, st.session_state.user_id, watching)
            st.rerun()

    # Metrics row
    m1, m2, m3, m4, m5 = st.columns(5)
    with m1: st.metric("CORP SCORE", score)
    with m2: st.metric("⭐ STARS", f"{stars:,}")
    with m3: st.metric("LANGUAGE", row.get("language") or "—")
    with m4: st.metric("CATEGORY", cat or "—")
    with m5: st.metric("VIBE", vibe or "—")

    # Gauge + progress bar + summary
    g, bar = st.columns([1, 3])
    with g:
        st.plotly_chart(gauge_chart(hype, "AI HYPE"), use_container_width=True,
                        config={"displayModeBar": False},
                        key=f"gauge_{full_name}")
    with bar:
        st.markdown('<div class="label" style="margin-bottom:4px">CORPORATE SIGNAL STRENGTH</div>',
                    unsafe_allow_html=True)
        pct = min(score / 150, 1.0)
        bc  = "#00FFAA" if pct > .4 else "#FFD700" if pct > .15 else "#FF3333"
        st.markdown(
            f'<div style="background:#1E293B;border-radius:3px;height:8px;margin-bottom:8px;">'
            f'<div style="background:{bc};width:{int(pct*100)}%;height:8px;border-radius:3px;"></div></div>',
            unsafe_allow_html=True)
        if summary:
            st.markdown(
                f'<div style="font-size:12px;color:#94A3B8;border-left:2px solid #1E293B;'
                f'padding-left:12px;font-style:italic;">{summary}</div>',
                unsafe_allow_html=True)

    # Top adopters pills
    rsig = sig[sig["repo_id"] == repo_id] if not sig.empty and repo_id else pd.DataFrame()
    if not rsig.empty:
        top = rsig.groupby("company")["signal_score"].sum().sort_values(ascending=False).head(5)
        st.markdown('<div class="label" style="margin:8px 0 6px">TOP ADOPTERS</div>',
                    unsafe_allow_html=True)
        pills = " ".join([
            f'<span style="background:#0EA5E915;border:1px solid #0EA5E944;color:#38BDF8;'
            f'border-radius:3px;padding:2px 8px;font-size:11px;font-family:JetBrains Mono;">'
            f'{co} +{sc}</span>'
            for co, sc in top.items() if co])
        st.markdown(pills, unsafe_allow_html=True)

    # Speechmatics voice alert
    audio_file = f"assets/alert_{full_name.replace('/','_')}.wav"

    if rating == "BUY":
        col_audio, col_btn = st.columns([3, 1])
        with col_btn:
            if st.button("🔊 Play Signal", key=f"audio_{full_name}"):
                from agents.voice_alert import narrate_signal
                top_co = ""
                if not rsig.empty:
                    top_co = rsig.sort_values("signal_score", ascending=False).iloc[0].get("company", "")
                with st.spinner("Generating voice alert..."):
                    success = narrate_signal(full_name, score, top_co)
                if success is True:
                    st.rerun()
                elif success:
                    st.error(f"Speechmatics error: {success}")
                else:
                    st.error("Speechmatics call returned False — check terminal for details")
        with col_audio:
            if os.path.exists(audio_file):
                st.audio(audio_file, format="audio/wav")

    # Historical trend
    if repo_id:
        hist = load_score_history(str(repo_id))
        if not hist.empty and len(hist) > 1:
            with st.expander("📈 Score History"):
                st.plotly_chart(history_chart(hist), use_container_width=True,
                                config={"displayModeBar": False},
                                key=f"history_{full_name}")  # or use repo_id

    # Hiring dossier
    try:
        dossier = json.loads(row.get("hiring_dossier") or "{}")
    except Exception:
        dossier = {}
    contribs = dossier.get("contributors", [])
    if contribs:
        acqui  = dossier.get("acqui_hire_rating", "Unknown")
        ac_col = {"High": "#00FFAA", "Medium": "#FFD700", "Low": "#FF3333"}.get(acqui, "#64748B")
        with st.expander(f"🎯 HIRING DOSSIER — Acqui-hire: {acqui}"):
            st.markdown(
                f'<div style="font-size:12px;color:{ac_col};font-family:JetBrains Mono;margin-bottom:8px;">'
                f'⚡ {dossier.get("acqui_hire_rationale","")}</div>', unsafe_allow_html=True)
            if (rf := dossier.get("red_flags","N/A")) not in ("N/A", ""):
                st.markdown(f'<div style="font-size:11px;color:#FF3333;margin-bottom:10px;">⚠ {rf}</div>',
                            unsafe_allow_html=True)
            for i, c in enumerate(contribs[:5]):
                st.markdown(
                    f'<div style="display:flex;align-items:center;padding:6px 0;'
                    f'border-bottom:1px solid #1E293B;">'
                    f'<span style="color:#475569;font-family:JetBrains Mono;width:20px">{i+1}</span>'
                    f'<a href="{c.get("profile_url","#")}" target="_blank" '
                    f'style="color:#94A3B8;flex:1;font-size:13px">{c.get("login","—")}</a>'
                    f'<span style="color:#00FFAA;font-family:JetBrains Mono;font-size:11px">'
                    f'{c.get("contributions",0):,} commits</span></div>',
                    unsafe_allow_html=True)

    st.markdown("<hr>", unsafe_allow_html=True)


# ── Pipeline Monitor ──────────────────────────────────────────────────────────
def view_pipeline():
    st.markdown('<div class="label" style="margin-bottom:16px">Agent Activity Feed</div>',
                unsafe_allow_html=True)
    df = load_pipeline_runs()
    if df.empty:
        st.info("No pipeline runs yet. Run `python main.py` to start the agents.")
        return
    icons   = {"success": "✅", "failed": "🚨", "partial": "⚠️", "started": "🔄"}
    p_col   = {"harvest": "#00D4FF", "enrich": "#FFD700", "analyze": "#00FFAA", "alert": "#FF3333"}
    for _, r in df.iterrows():
        icon = icons.get(r.get("status",""), "◉")
        phase, status = r.get("phase","—"), r.get("status","—")
        dur   = f'{r["duration_seconds"]:.1f}s' if r.get("duration_seconds") else "—"
        ts    = str(r.get("started_at",""))[:16]
        sc    = "color:#00FFAA" if status == "success" else "color:#FF3333" if status == "failed" else "color:#64748B"
        err   = str(r.get("error_message") or "")
        detail = err[:70] if err else f'repos={r.get("repos_processed",0)} signals={r.get("signals_detected",0)}'
        c1,c2,c3,c4,c5 = st.columns([.4,1.5,1,1,2.5])
        with c1: st.markdown(f'<div style="font-size:16px;margin-top:4px">{icon}</div>', unsafe_allow_html=True)
        with c2: st.markdown(f'<div class="mono" style="font-size:12px;color:{p_col.get(phase,"#64748B")}">{phase.upper()}</div>', unsafe_allow_html=True)
        with c3: st.markdown(f'<div class="mono" style="font-size:12px;{sc}">{status}</div>', unsafe_allow_html=True)
        with c4: st.markdown(f'<div class="mono" style="font-size:11px;color:#475569">{dur}</div>', unsafe_allow_html=True)
        with c5: st.markdown(f'<div class="mono" style="font-size:11px;color:#334155">{ts} · {detail}</div>', unsafe_allow_html=True)
        st.markdown("<hr>", unsafe_allow_html=True)


# ── Alert Log ─────────────────────────────────────────────────────────────────
def view_alerts():
    st.markdown('<div class="label" style="margin-bottom:16px">Fired Alerts</div>',
                unsafe_allow_html=True)
    df = load_alert_log()
    if df.empty:
        st.info("No alerts yet. Set SLACK_WEBHOOK_URL or DISCORD_WEBHOOK_URL in .env")
        return
    for _, r in df.iterrows():
        rating = r.get("rating","—")
        rc = {"BUY":"#00FFAA","HOLD":"#FFD700","SELL":"#FF3333"}.get(rating,"#64748B")
        ts = str(r.get("alerted_at",""))[:16]
        c1,c2,c3,c4 = st.columns([3,1.2,1.2,1.5])
        with c1: st.markdown(f'<div class="mono" style="font-size:13px;color:#94A3B8">{r.get("repo_full_name","—")}</div>', unsafe_allow_html=True)
        with c2: st.markdown(f'<div class="mono" style="font-size:12px;color:{rc}">{rating}</div>', unsafe_allow_html=True)
        with c3: st.markdown(f'<div class="mono" style="font-size:12px;color:#00FFAA">{r.get("corporate_score_at_alert",0)}</div>', unsafe_allow_html=True)
        with c4: st.markdown(f'<div class="mono" style="font-size:11px;color:#334155">{ts}</div>', unsafe_allow_html=True)
        st.markdown("<hr>", unsafe_allow_html=True)


# ── Watchlist View ────────────────────────────────────────────────────────────
def view_watchlist(repos_df: pd.DataFrame, sig: pd.DataFrame):
    wl = load_watchlist(st.session_state.user_id)
    if not wl:
        st.info("☆ No repos pinned. Click 'Watch' on any card in Intelligence Feed.")
        return
    watched = repos_df[repos_df["full_name"].isin(wl)] if not repos_df.empty else pd.DataFrame()
    if watched.empty:
        st.info("Watched repos not in current dataset. Broaden your filters.")
        return
    st.markdown(f'<div class="label" style="margin-bottom:16px">Watching {len(watched)} Repos</div>',
                unsafe_allow_html=True)
    for _, row in watched.iterrows():
        render_card(row, sig, wl)


# ── Sidebar ───────────────────────────────────────────────────────────────────
def render_sidebar(stats: dict) -> dict:
    with st.sidebar:
        st.markdown(
            '<div class="ra-sub">RepoAlpha · Enterprise v2</div>'
            '<div class="ra-title">WAR ROOM</div>'
            f'<div class="mono" style="font-size:10px;color:#334155;margin-top:4px">'
            f'{datetime.now().strftime("%Y-%m-%d %H:%M")}</div>',
            unsafe_allow_html=True)
        st.markdown("---")

        st.markdown('<div class="label" style="margin-bottom:8px">Intelligence Status</div>',
                    unsafe_allow_html=True)
        st.metric("Repos Tracked", stats["repos"])
        st.metric("Corp. Signals",  stats["signals"])
        epct = int(stats["enriched"] / max(stats["stargazers"],1) * 100)
        st.metric("Enrichment", f"{epct}%", delta=f"{stats['enriched']:,} profiles")
        st.metric("Alerts Fired", stats["alerts"])

        st.markdown("---")
        st.markdown('<div class="label" style="margin-bottom:8px">Navigation</div>',
                    unsafe_allow_html=True)
        view = st.radio("View", [
            "🔥 Intelligence Feed",
            "⭐ My Watchlist",
            "🚨 Alert Log",
            "🔧 Pipeline Monitor",
        ], label_visibility="collapsed")

        st.markdown("---")
        st.markdown('<div class="label" style="margin-bottom:8px">Filters</div>',
                    unsafe_allow_html=True)
        category = st.selectbox("Category", ["All","AI/ML","DevTools","Security",
                                              "Data/Analytics","Infrastructure","Web3","Robotics","Other"])
        rating_f  = st.selectbox("Rating",   ["All","BUY","HOLD","SELL"])
        min_score = st.slider("Min Corp. Score", 0, 100, 0, step=5)
        show_risk = st.toggle("🚨 IP Risk Only (AGPL/GPL)", value=False)

        st.markdown("---")
        st.markdown('<div class="label" style="margin-bottom:8px">Export</div>',
                    unsafe_allow_html=True)
        if st.button("⬇ Download CSV"):
            st.session_state["export"] = "csv"
        if st.button("⬇ Download JSON"):
            st.session_state["export"] = "json"

        api_url = os.environ.get("API_URL", "http://localhost:8000")
        st.markdown(
            f'<div class="mono" style="font-size:10px;color:#334155;margin-top:8px">'
            f'API: <a href="{api_url}/docs" target="_blank" style="color:#0EA5E9">Swagger Docs ↗</a></div>',
            unsafe_allow_html=True)
        st.markdown("---")
        if st.button("🔄 Refresh Data"):
            st.cache_data.clear()
            st.rerun()

    return dict(view=view, category=category, rating_f=rating_f,
                min_score=min_score, show_risk=show_risk)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    stats   = load_stats()
    filters = render_sidebar(stats)
    repos   = load_repos(50, filters["category"], filters["min_score"], filters["rating_f"])
    sig     = load_signals()
    wl      = load_watchlist(st.session_state.user_id)

    if filters["show_risk"] and not repos.empty:
        repos = repos[repos["license_color"] == "red"]

    # Export
    if st.session_state.get("export") == "csv" and not repos.empty:
        st.sidebar.download_button("💾 Save CSV", export_csv(repos),
                                   "repoalpha.csv", "text/csv")
        del st.session_state["export"]
    elif st.session_state.get("export") == "json" and not repos.empty:
        st.sidebar.download_button("💾 Save JSON",
                                   repos.to_json(orient="records", indent=2).encode(),
                                   "repoalpha.json", "application/json")
        del st.session_state["export"]

    # Header
    st.markdown(
        '<div class="ra-sub">Bright Data AI Hackathon 2026 · Enterprise Intelligence Platform</div>'
        '<div class="ra-title">REPOALPHA</div>'
        '<div class="mono" style="font-size:12px;color:#64748B;margin-top:4px">'
        '// Corporate Signal Engine · Semantic Clustering · Real-time Alerts //</div>',
        unsafe_allow_html=True)

    render_ticker(repos)
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    # KPIs
    k1,k2,k3,k4,k5,k6 = st.columns(6)
    buy   = len(repos[repos["rating"]=="BUY"])  if not repos.empty else 0
    risk  = len(repos[repos["license_color"]=="red"]) if not repos.empty else 0
    ahype = repos["hype_display"].mean() if not repos.empty else 0
    tops  = repos["corporate_score"].max() if not repos.empty else 0
    ucos  = sig["company"].nunique()       if not sig.empty   else 0
    with k1: st.metric("🟢 BUY SIGNALS", buy)
    with k2: st.metric("🚨 IP RISKS",    risk)
    with k3: st.metric("HYPE AVG",       f"{ahype:.1f}/10")
    with k4: st.metric("TOP SCORE",      int(tops))
    with k5: st.metric("COMPANIES",      ucos)
    with k6: st.metric("ALERTS",         stats["alerts"])
    st.markdown("---")

    # Route views
    v = filters["view"]
    if v == "🔧 Pipeline Monitor": view_pipeline();       return
    if v == "🚨 Alert Log":        view_alerts();         return
    if v == "⭐ My Watchlist":     view_watchlist(repos, sig); return

    # Intelligence Feed
    left, right = st.columns([3, 1.2])

    with right:
        st.markdown('<div class="label" style="margin-bottom:10px">Corporate Signal Map</div>',
                    unsafe_allow_html=True)
        if not sig.empty:
            st.plotly_chart(treemap_chart(sig), use_container_width=True,
                            config={"displayModeBar": False})

        st.markdown('<div class="label" style="margin:12px 0 8px">Score Leaderboard</div>',
                    unsafe_allow_html=True)
        if not repos.empty:
            st.plotly_chart(score_velocity_chart(repos), use_container_width=True,
                            config={"displayModeBar": False})

        st.markdown('<div class="label" style="margin:12px 0 8px">Top Signals</div>',
                    unsafe_allow_html=True)
        if not sig.empty:
            top_s = (sig[["company","github_login","signal_score"]]
                     .dropna(subset=["company"])
                     .sort_values("signal_score", ascending=False)
                     .head(12).reset_index(drop=True))
            top_s.index += 1
            st.dataframe(top_s.rename(columns={"company":"Company",
                                                "github_login":"User",
                                                "signal_score":"Pts"}),
                         use_container_width=True)

        st.markdown('<div class="label" style="margin:12px 0 8px">Last Runs</div>',
                    unsafe_allow_html=True)
        runs = load_pipeline_runs(4)
        if not runs.empty:
            pc = {"harvest":"#00D4FF","enrich":"#FFD700","analyze":"#00FFAA","alert":"#FF3333"}
            si = {"success":"✅","failed":"🚨","partial":"⚠️","started":"🔄"}
            for _, r in runs.iterrows():
                phase = r.get("phase","—")
                st.markdown(
                    f'<div class="mono" style="font-size:11px;color:#475569;margin:2px 0">'
                    f'{si.get(r.get("status",""),"◉")} '
                    f'<span style="color:{pc.get(phase,"#64748B")}">{phase.upper()}</span> · '
                    f'{str(r.get("started_at",""))[:16]}</div>',
                    unsafe_allow_html=True)

    with left:
        if repos.empty:
            st.info("📡 No data yet. Run `python main.py --demo` to seed demo data.")
        else:
            st.markdown(
                f'<div class="label" style="margin-bottom:14px">Showing {len(repos)} repositories</div>',
                unsafe_allow_html=True)
            for _, row in repos.iterrows():
                render_card(row, sig, wl)


if __name__ == "__main__":
    main()