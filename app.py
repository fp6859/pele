import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import time
import re
from urllib.parse import quote_plus

st.set_page_config(page_title="Footballdatabase.eu Player Seasons", layout="wide")
st.title("Footballdatabase.eu – Player Search & Best Seasons")
st.caption("Proxy index based on available career stats. Official FBDB Index is not tabulated per season.")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

@st.cache_data(ttl=3600)
def search_players(query: str, max_results: int = 15):
    """Search via the site's search results page and extract player links."""
    if not query or len(query.strip()) < 2:
        return []
    
    url = f"https://www.footballdatabase.eu/en/search_results?search={quote_plus(query.strip())}"
    try:
        time.sleep(0.8)
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        
        results = []
        # Look for player detail links
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/player/details/" in href:
                name = a.get_text(strip=True)
                if name and len(name) > 2:
                    full_url = href if href.startswith("http") else f"https://www.footballdatabase.eu{href}"
                    # Extract ID if present
                    match = re.search(r"/player/details/(\d+)", href)
                    pid = match.group(1) if match else None
                    results.append({
                        "name": name,
                        "url": full_url,
                        "id": pid
                    })
        
        # Deduplicate by URL
        seen = set()
        unique = []
        for r in results:
            if r["url"] not in seen:
                seen.add(r["url"])
                unique.append(r)
        return unique[:max_results]
    except Exception as e:
        st.warning(f"Search failed: {e}")
        return []

@st.cache_data(ttl=86400)
def get_player_career(player_url: str):
    """Fetch player page and extract career statistics table."""
    try:
        time.sleep(1.0)
        r = requests.get(player_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        
        # Find the main career statistics table (usually the largest one)
        tables = soup.find_all("table")
        if not tables:
            return pd.DataFrame(), "No tables found"
        
        # Prefer tables that look like career stats
        best_df = None
        for table in tables:
            try:
                dfs = pd.read_html(str(table))
                if dfs:
                    df = dfs[0]
                    # Heuristic: career tables have Season / Club / Games-like columns
                    cols_lower = [str(c).lower() for c in df.columns]
                    if any(k in " ".join(cols_lower) for k in ["season", "saison", "games", "match", "but", "goal"]):
                        best_df = df
                        break
            except Exception:
                continue
        
        if best_df is None and tables:
            # Fallback to the largest table
            try:
                best_df = pd.read_html(str(max(tables, key=lambda t: len(str(t)))))[0]
            except Exception:
                return pd.DataFrame(), "Could not parse any table"
        
        if best_df is None or best_df.empty:
            return pd.DataFrame(), "Empty table"
        
        # Basic cleaning
        best_df = best_df.dropna(how="all")
        best_df.columns = [str(c).strip() for c in best_df.columns]
        
        return best_df, None
    except Exception as e:
        return pd.DataFrame(), str(e)

def compute_proxy_index(df: pd.DataFrame) -> pd.DataFrame:
    """
    Transparent proxy for ranking seasons.
    Weights: Goals × 4 + Assists × 2.5 + Games × 0.4, then adjusted by minutes when available.
    You can edit the weights below.
    """
    if df.empty:
        return df
    
    df = df.copy()
    
    # Try to identify common column names (case-insensitive)
    col_map = {c.lower(): c for c in df.columns}
    
    def find_col(*candidates):
        for cand in candidates:
            for k, orig in col_map.items():
                if cand in k:
                    return orig
        return None
    
    season_col = find_col("season", "saison", "year")
    club_col = find_col("club", "team", "equipe")
    games_col = find_col("game", "match", "pj", "played")
    goals_col = find_col("goal", "but", "goles")
    assists_col = find_col("assist", "pass", "p.d.", "passe")
    minutes_col = find_col("minute", "min", "minuta")
    efficiency_col = find_col("efficien", "eff.", "eficiencia")
    
    # Convert numeric columns
    for col in [games_col, goals_col, assists_col, minutes_col]:
        if col and col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    
    # Build proxy index
    goals = df[goals_col] if goals_col else 0
    assists = df[assists_col] if assists_col else 0
    games = df[games_col] if games_col else 0
    minutes = df[minutes_col] if minutes_col else None
    
    df["proxy_index"] = goals * 4.0 + assists * 2.5 + games * 0.4
    
    if minutes is not None and minutes.sum() > 0:
        # Per-90 style adjustment
        per90 = (minutes / 90).clip(lower=1)
        df["proxy_index"] = df["proxy_index"] / per90 * 10  # scale for readability
    
    # Sort
    df = df.sort_values("proxy_index", ascending=False).reset_index(drop=True)
    
    # Keep useful columns for display
    display_cols = [c for c in [season_col, club_col, games_col, goals_col, assists_col, 
                                minutes_col, efficiency_col, "proxy_index"] if c and c in df.columns]
    return df[display_cols] if display_cols else df

# --- UI ---
st.subheader("1. Search for a player")
query = st.text_input("Player name (e.g. Messi, Haaland, Yamal)", placeholder="Start typing...")

col1, col2 = st.columns([3, 1])
with col1:
    if query:
        with st.spinner("Searching..."):
            results = search_players(query)
        
        if results:
            options = [f"{r['name']} (ID: {r['id'] or '?'})" for r in results]
            choice_idx = st.selectbox("Select a player", range(len(options)), 
                                       format_func=lambda i: options[i])
            selected = results[choice_idx]
            st.info(f"Selected: [{selected['name']}]({selected['url']})")
        else:
            st.warning("No players found. Try a different spelling or paste a direct URL below.")
            selected = None
    else:
        selected = None

with col2:
    st.write("")  # spacer

st.subheader("2. Or paste a direct player URL")
manual_url = st.text_input(
    "Player page URL",
    placeholder="https://www.footballdatabase.eu/en/player/details/10973-lionel-messi"
)

# Determine which URL to use
player_url = None
if selected and st.button("Load selected player"):
    player_url = selected["url"]
elif manual_url and st.button("Load from URL"):
    player_url = manual_url.strip()

if player_url:
    st.divider()
    st.subheader("Career statistics & best seasons")
    
    with st.spinner("Fetching and parsing player page..."):
        df, error = get_player_career(player_url)
    
    if error:
        st.error(f"Error: {error}")
    elif df.empty:
        st.warning("No career data extracted. The page structure may have changed.")
    else:
        ranked = compute_proxy_index(df)
        
        st.markdown("### Best 3 seasons (by proxy index)")
        st.dataframe(ranked.head(3), use_container_width=True)
        
        st.markdown("### All seasons")
        st.dataframe(ranked, use_container_width=True)
        
        # Simple chart if we have seasons
        if "proxy_index" in ranked.columns and len(ranked) > 1:
            st.markdown("### Proxy index by season")
            chart_df = ranked.head(15).iloc[::-1]  # recent-ish for chart
            st.bar_chart(chart_df.set_index(chart_df.columns[0])["proxy_index"])

st.sidebar.header("About the proxy index")
st.sidebar.markdown("""
The official **FBDB Index** is an automatic figure based on a player’s statistical performance in matches.

Because it is not reliably listed per season in the career table, this app uses a transparent proxy:

- Goals × 4  
- Assists × 2.5  
- Games × 0.4  
- Adjusted by minutes played (per-90 style) when available

You can edit the weights in the `compute_proxy_index` function.
""")

st.sidebar.markdown("---")
st.sidebar.caption("Personal use only. Respect footballdatabase.eu terms and rate limits. Data is scraped live and may be incomplete.")
