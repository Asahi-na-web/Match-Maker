import streamlit as st
import pandas as pd
import json
import random
import gspread
from google.oauth2.service_account import Credentials
from itertools import combinations, permutations

# --- ページ設定 & デザイン ---
st.set_page_config(page_title="MOBA Team Matchmaker", layout="wide")

# カスタムCSSで色遣いを調整
st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; }
    .red-team { background-color: #ffeef0; padding: 20px; border-radius: 10px; border-left: 5px solid #ff4b4b; }
    .white-team { background-color: #ffffff; padding: 20px; border-radius: 10px; border: 1px solid #dee2e6; border-left: 5px solid #7d7d7d; }
    .player-card { background-color: white; padding: 10px; border-radius: 5px; margin-bottom: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    </style>
    """, unsafe_allow_html=True)

# --- 設定 ---
ROLES = ["上キャ", "上学習", "中央", "下キャ", "下学習"]

# --- Google Sheets 接続設定 ---
def get_gspread_client():
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
    credentials = Credentials.from_service_account_info(creds_dict, scopes=scope)
    return gspread.authorize(credentials)

def load_from_sheets():
    try:
        client = get_gspread_client()
        sheet = client.open_by_url(st.secrets["spreadsheet_url"]).sheet1
        records = sheet.get_all_records()
        data = {}
        for r in records:
            data[r['name']] = {
                'active': bool(r['active']),
                'wins': int(r['wins']),
                'total': int(r['total']),
                'omw': float(r['omw']),
                'last_teammates': json.loads(r['last_teammates']) if r['last_teammates'] else [],
                'opponents': json.loads(r['opponents']) if r['opponents'] else [],
                'roles': json.loads(r['roles'])
            }
        return data
    except Exception as e:
        st.error(f"データ読み込みエラー: {e}")
        return {}

def save_to_sheets(players):
    try:
        client = get_gspread_client()
        sheet = client.open_by_url(st.secrets["spreadsheet_url"]).sheet1
        sheet.clear()
        rows = [["name", "active", "wins", "total", "omw", "last_teammates", "opponents", "roles"]]
        for n, p in players.items():
            rows.append([n, int(p['active']), p['wins'], p['total'], p['omw'],
                         json.dumps(p['last_teammates'], ensure_ascii=False),
                         json.dumps(p['opponents'], ensure_ascii=False),
                         json.dumps(p['roles'], ensure_ascii=False)])
        sheet.update('A1', rows)
    except Exception as e:
        st.error(f"データ保存エラー: {e}")

# --- ロジック ---
class ProfessionalTeamSystem:
    def __init__(self):
        if 'players' not in st.session_state:
            st.session_state.players = load_from_sheets()
            st.session_state.fixed_pairs = []
            st.session_state.last_match_players = []
            st.session_state.matches = []
            st.session_state.page = "REGISTRATION"

    def calculate_win_rate(self, name):
        p = st.session_state.players[name]
        return p['wins'] / p['total'] if p['total'] > 0 else 0

    def update_omw(self, match_idx, winner_side):
        res = st.session_state.matches[match_idx]
        rm = list(res["赤チーム"].values()); wm = list(res["白チーム"].values())
        for side in ["赤チーム", "白チーム"]:
            win = (side == winner_side)
            m = rm if side == "赤チーム" else wm; o = wm if side == "赤チーム" else rm
            for name in m:
                p = st.session_state.players[name]; p['total'] += 1
                if win: p['wins'] += 1
                p['opponents'].extend(o); p['last_teammates'] = m
        save_to_sheets(st.session_state.players)

    def solve_best_distribution(self, names, balance_mode):
        pool = list(combinations(names, 5)); random.shuffle(pool); cands = []; fallback_cands = []
        for ta in pool:
            tb = [n for n in names if n not in ta]; pf = False
            for p in st.session_state.fixed_pairs:
                if (p[0] in ta and p[1] not in ta) or (p[0] in tb and p[1] not in tb): pf = True; break
            ra, wa = self.assign_roles_flexible(ta); rb, wb = self.assign_roles_flexible(tb); rep = 0
            for n in ta: rep += len(set(st.session_state.players[n].get('last_teammates', [])) & set(ta))
            d = {"赤チーム": ra, "白チーム": rb, "warn": (wa or wb or pf), "rep": rep, "done": False}
            if balance_mode:
                wa_list = [self.calculate_win_rate(n) for n in ta]; wb_list = [self.calculate_win_rate(n) for n in tb]
                d["diff"] = abs(sum(wa_list) - sum(wb_list))
            if not d["warn"] and rep <= 2: cands.append(d)
            else: fallback_cands.append(d)
            if len(cands) > 50: break
        res_list = cands if cands else fallback_cands
        return min(res_list, key=lambda x: (x.get("diff", 0), x["warn"], x["rep"])) if res_list else None

    def assign_roles_flexible(self, members):
        for p in permutations(members):
            t = {}
            for i, r in enumerate(ROLES):
                if r in st.session_state.players[p[i]]['roles']: t[r] = p[i]
                else: break
            if len(t) == 5: return t, False
        return {ROLES[i]: members[i] for i in range(5)}, True

sys = ProfessionalTeamSystem()

# --- 画面遷移 ---
with st.sidebar:
    st.title("Let's カスタム!!")
    st.info(f"現在の工程: {st.session_state.page}")
    if st.button("データ強制同期"):
        st.session_state.players = load_from_sheets()
        st.rerun()
    if st.button("最初からやり直す", type="secondary"):
        st.session_state.page = "REGISTRATION"
        st.rerun()

# 1. 登録画面
if st.session_state.page == "REGISTRATION":
    st.header(" プレイヤー管理")
    col_f1, col_f2 = st.columns([1, 1])
    with col_f1:
        with st.form("add_p", clear_on_submit=True):
            ni = st.text_input("プレイヤー名:")
            rm = st.selectbox("メインロール", ROLES)
            rc = st.multiselect("サブロール（複数可）", ROLES, default=[rm])
            if st.form_submit_button("登録・更新"):
                if ni:
                    st.session_state.players[ni] = {'roles': list(set([rm]+rc)), 'active': True, 'wins': 0, 'total': 0, 'omw': 0.0, 'last_teammates': [], 'opponents': []}
                    save_to_sheets(st.session_state.players); st.success(f"{ni} を登録しました"); st.rerun()
    with col_f2:
        if st.button("全戦績をリセット"):
            for p in st.session_state.players.values(): p.update({'wins':0,'total':0,'last_teammates':[],'opponents':[]})
            save_to_sheets(st.session_state.players); st.rerun()

    st.divider()
    active_count = sum(1 for p in st.session_state.players.values() if p['active'])
    st.subheader(f"参加・名簿 (現在 {active_count}名 選択中)")
    
    cols = st.columns(3)
    for i, (n, p) in enumerate(st.session_state.players.items()):
        with cols[i % 3]:
            with st.container():
                st.markdown(f"<div class='player-card'>", unsafe_allow_html=True)
                c = st.checkbox(f"**{n}** ({','.join(p['roles'])})", value=p['active'], key=f"c_{n}")
                if c != p['active']:
                    st.session_state.players[n]['active'] = c; save_to_sheets(st.session_state.players)
                st.markdown("</div>", unsafe_allow_html=True)

    if st.button("ペア設定へ進む ", type="primary"):
        st.session_state.page = "PAIRING"; st.rerun()

elif st.session_state.page == "PAIRING":
    st.header(" ペア固定設定")
    pl = sorted([n for n, p in st.session_state.players.items() if p['active']])
    if len(pl) < 10:
        st.warning(f"10名必要です（現在{len(pl)}名）。管理画面に戻ってチェックを入れてください。")
        if st.button(" 戻る"): st.session_state.page = "REGISTRATION"; st.rerun()
    else:
        c1, c2 = st.columns(2)
        da = c1.selectbox("プレイヤーA", pl)
        db = c2.selectbox("プレイヤーB", pl)
        if st.button("この二人を同じチームにする"):
            st.session_state.fixed_pairs.append([da, db]); st.success(f"固定完了: {da} & {db}")
        
        if st.session_state.fixed_pairs:
            st.write("現在の固定ペア:")
            for p in st.session_state.fixed_pairs: st.text(f"・{p[0]} & {p[1]}")
            if st.button("固定をすべて解除"): st.session_state.fixed_pairs = []; st.rerun()
        
        if st.button("チーム分け実行 ", type="primary"):
            st.session_state.page = "CONFIG"; st.rerun()

# 3. 試合設定画面
elif st.session_state.page == "CONFIG":
    st.header(" チーム分け設定")
    tc = st.radio("生成する試合数:", [1, 2, 3], horizontal=True)
    mode = st.toggle("勝率バランスを考慮する", value=True)
    
    if st.button("チームを自動生成！", type="primary"):
        act_n = [n for n, p in st.session_state.players.items() if p['active']]
        # 優先順位付け
        np = [n for n in act_n if n not in st.session_state.last_match_players]
        pl_prev = [n for n in act_n if n in st.session_state.last_match_players]
        random.shuffle(np); random.shuffle(pl_prev); sel = (np + pl_prev)[:tc*10]
        st.session_state.last_match_players = sel
        
        st.session_state.matches = []
        for i in range(tc):
            st.session_state.matches.append(sys.solve_best_distribution(sel[i*10:(i+1)*10], mode))
        st.session_state.page = "RESULT"; st.rerun()

# 4. 試合結果入力（ここが今回のメイン改善）
elif st.session_state.page == "RESULT":
    st.header(" 対戦カード & 結果入力")
    for i, m in enumerate(st.session_state.matches):
        if not m: continue
        st.subheader(f"第 {i+1} 試合")
        col_r, col_w = st.columns(2)
        
        with col_r:
            st.markdown(f"<div class='red-team'><h3>赤チーム {'⚠️' if m['warn'] else ''}</h3>", unsafe_allow_html=True)
            for r, n in m["赤チーム"].items(): st.write(f"**{r}**: {n}")
            if st.button(f"赤チームの勝利！", key=f"win_r_{i}", disabled=m["done"]):
                sys.update_omw(i, "赤チーム"); m["done"] = True
                st.balloons()
                st.success("結果を保存しました。ペア設定画面に戻ります。")
                st.session_state.page = "PAIRING" # ペア設定画面へ戻る（やり直し可能）
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
            
        with col_w:
            st.markdown(f"<div class='white-team'><h3>白チーム</h3>", unsafe_allow_html=True)
            for r, n in m["白チーム"].items(): st.write(f"**{r}**: {n}")
            if st.button(f"白チームの勝利！", key=f"win_w_{i}", disabled=m["done"]):
                sys.update_omw(i, "白チーム"); m["done"] = True
                st.balloons()
                st.success("結果を保存しました。ペア設定画面に戻ります。")
                st.session_state.page = "PAIRING" # ペア設定画面へ戻る（やり直し可能）
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
    
    st.divider()
    if st.button("全試合を終えて戦績を確認する"):
        st.session_state.page = "SUMMARY"; st.rerun()

# 5. 戦績表示（勝率順に並び替え）
elif st.session_state.page == "SUMMARY":
    st.header("本日の戦績ランキング")
    data_list = []
    for n, p in st.session_state.players.items():
        if p['total'] > 0:
            wr = sys.calculate_win_rate(n)
            data_list.append({
                "名前": n,
                "勝率数値": wr, # ソート用
                "勝率": f"{int(wr*100)}%",
                "試合数": p['total'],
                "勝ち": p['wins'],
                "負け": p['total'] - p['wins']
            })
    
    if data_list:
        # 勝率数値で降順ソート
        df = pd.DataFrame(data_list).sort_values(by="勝率数値", ascending=False).drop(columns=["勝率数値"])
        st.table(df)
    else:
        st.info("まだ試合データがありません。")

    if st.button("トップ（登録画面）に戻る"):
        st.session_state.page = "REGISTRATION"; st.rerun()