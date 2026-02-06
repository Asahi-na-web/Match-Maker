import streamlit as st
import pandas as pd
import json
import random
import gspread
from google.oauth2.service_account import Credentials
from itertools import combinations, permutations
import numpy as np

# --- ページ設定 & デザイン ---
st.set_page_config(page_title="MOBA Team Matchmaker", layout="wide")

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
@st.cache_resource
def get_gspread_client():
    try:
        scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scope)
        return gspread.authorize(credentials)
    except Exception as e:
        st.error(f"認証エラー: {e}")
        return None

def load_from_sheets():
    try:
        client = get_gspread_client()
        if not client: return {}
        sheet = client.open_by_url(st.secrets["spreadsheet_url"]).sheet1
        records = sheet.get_all_records()
        data = {}
        for r in records:
            roles_list = json.loads(r['roles']) if r.get('roles') else ROLES.copy()
            data[r['name']] = {
                'active': bool(r['active']), 'wins': int(r['wins'] or 0), 'total': int(r['total'] or 0),
                'omw': float(r['omw'] or 0.0), 'last_teammates': json.loads(r['last_teammates']) if r.get('last_teammates') else [],
                'opponents': json.loads(r['opponents']) if r.get('opponents') else [], 'roles': roles_list
            }
        return data
    except Exception as e:
        st.error(f"データ読み込みエラー: {e}"); return {}

def save_to_sheets(players):
    try:
        client = get_gspread_client()
        if not client: return
        sheet = client.open_by_url(st.secrets["spreadsheet_url"]).sheet1
        rows = [["name", "active", "wins", "total", "omw", "last_teammates", "opponents", "roles"]]
        for n, p in players.items():
            rows.append([n, int(p['active']), p['wins'], p['total'], p['omw'],
                         json.dumps(p['last_teammates'], ensure_ascii=False),
                         json.dumps(p['opponents'], ensure_ascii=False),
                         json.dumps(p['roles'], ensure_ascii=False)])
        sheet.update(values=rows, range_name='A1', value_input_option='USER_ENTERED')
    except Exception as e:
        st.error(f"保存エラー: {e}")

# --- ロジック ---
class ProfessionalTeamSystem:
    def __init__(self):
        if 'players' not in st.session_state:
            st.session_state.players = load_from_sheets()
            st.session_state.fixed_pairs = []; st.session_state.last_match_players = []
            st.session_state.matches = []; st.session_state.page = "REGISTRATION"

    def calculate_win_rate(self, name):
        p = st.session_state.players.get(name)
        return p['wins'] / p['total'] if p and p['total'] > 0 else 0.0

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
        pool = list(combinations(names, 5)); random.shuffle(pool); cands = []
        
        for ta in pool:
            tb = [n for n in names if n not in ta]
            # 固定ペアチェック
            pf = any((p[0] in ta and p[1] not in ta) or (p[0] in tb and p[1] not in tb) for p in st.session_state.fixed_pairs)
            
            ra, score_a, wa = self.assign_roles_flexible(ta)
            rb, score_b, wb = self.assign_roles_flexible(tb)
            
            # 再会率（前回チームメイトだった数）
            rep = sum(len(set(st.session_state.players[n].get('last_teammates', [])) & set(ta)) for n in ta)
            
            d = {"赤チーム": ra, "白チーム": rb, "warn": (wa or wb or pf), "rep": rep, "done": False, "role_score": score_a + score_b}
            
            # 勝率計算
            wa_list = [self.calculate_win_rate(n) for n in ta]
            wb_list = [self.calculate_win_rate(n) for n in tb]
            d["diff"] = abs(sum(wa_list) - sum(wb_list))
            d["var_diff"] = abs(np.var(wa_list) - np.var(wb_list))
            
            cands.append(d)
            if len(cands) > 500: break

        if balance_mode:
            # 勝率合計の差 -> 分散の差 -> ロール満足度の順で最適化（再会制限なし）
            return min(cands, key=lambda x: (x["diff"], x["var_diff"], -x["role_score"]))
        else:
            # 再会率(低) -> ロール満足度(高) -> 勝率差の順で最適化
            return min(cands, key=lambda x: (x["rep"], -x["role_score"], x["warn"], x["diff"]))

    def assign_roles_flexible(self, members):
        best_assignment, max_score = None, -1
        for p in permutations(members):
            current_assignment, current_score, possible = {}, 0, True
            for i, r in enumerate(ROLES):
                p_roles = st.session_state.players[p[i]]['roles']
                if r in p_roles:
                    current_assignment[r] = p[i]
                    # 第一希望(インデックス0)なら100点、その他希望なら1点
                    current_score += 100 if p_roles[0] == r else 1
                else:
                    possible = False; break
            if possible and current_score > max_score:
                max_score = current_score; best_assignment = current_assignment
                if max_score >= 500: break
        
        if best_assignment: return best_assignment, max_score, False
        return {ROLES[i]: members[i] for i in range(5)}, 0, True

sys = ProfessionalTeamSystem()

# --- 画面遷移 ---
with st.sidebar:
    st.title("Let's カスタム!!")
    if st.button("データ強制同期"):
        st.cache_resource.clear(); st.session_state.players = load_from_sheets(); st.rerun()
    if st.button("最初からやり直す", type="secondary"):
        st.session_state.page = "REGISTRATION"; st.rerun()

# 1. 登録画面
if st.session_state.page == "REGISTRATION":
    st.header("👤 プレイヤー管理")
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        st.subheader("📝 個別登録・更新")
        with st.form("add_p", clear_on_submit=True):
            ni = st.text_input("プレイヤー名:")
            rm = st.selectbox("メインロール (第一希望)", ROLES)
            rc = st.multiselect("その他希望ロール (複数選択可)", ROLES)
            if st.form_submit_button("登録・更新"):
                if ni:
                    # 選択順に関わらず、メインを先頭にし、重複を除去してリスト化
                    final_roles = [rm] + [r for r in rc if r != rm]
                    st.session_state.players[ni] = {'roles': final_roles, 'active': True, 'wins': 0, 'total': 0, 'omw': 0.0, 'last_teammates': [], 'opponents': []}
                    save_to_sheets(st.session_state.players); st.success(f"{ni} 登録完了"); st.rerun()
    with col_f2:
        st.subheader("📋 テキスト一括登録")
        bulk_text = st.text_area("形式: 名前 (第一, その他1...)", placeholder="A (中央, 上キャ)\nB (下学習)", height=150)
        if st.button("一括適用"):
            if bulk_text:
                for line in bulk_text.split('\n'):
                    if "(" in line and ")" in line:
                        name = line.split("(")[0].strip()
                        roles = [r.strip() for r in line.split("(")[1].split(")")[0].split(",") if r.strip() in ROLES]
                        if name:
                            if name not in st.session_state.players: st.session_state.players[name] = {'wins':0, 'total':0, 'omw':0.0, 'last_teammates':[], 'opponents':[]}
                            st.session_state.players[name].update({'roles': roles if roles else ROLES.copy(), 'active': True})
                save_to_sheets(st.session_state.players); st.rerun()

    st.divider()
    active_count = sum(1 for p in st.session_state.players.values() if p['active'])
    st.subheader(f"参加名簿 ({active_count}名)")
    cols = st.columns(3)
    for i, (n, p) in enumerate(sorted(st.session_state.players.items())):
        with cols[i % 3]:
            st.markdown("<div class='player-card'>", unsafe_allow_html=True)
            r_disp = f"★{p['roles'][0]}" + (f", {', '.join(p['roles'][1:])}" if len(p['roles'])>1 else "")
            if st.checkbox(f"**{n}**\n({r_disp})", value=p['active'], key=f"c_{n}") != p['active']:
                st.session_state.players[n]['active'] = not p['active']; save_to_sheets(st.session_state.players); st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
    if st.button("ペア設定へ進む ➡️", type="primary"): st.session_state.page = "PAIRING"; st.rerun()

# 2. ペア設定
elif st.session_state.page == "PAIRING":
    st.header("🔗 ペア固定設定")
    pl = sorted([n for n, p in st.session_state.players.items() if p['active']])
    if len(pl) < 10:
        st.warning(f"現在{len(pl)}名です。10名選んでください。"); st.button("戻る", on_click=lambda: setattr(st.session_state, 'page', 'REGISTRATION'))
    else:
        c1, c2 = st.columns(2)
        da, db = c1.selectbox("A", pl), c2.selectbox("B", pl)
        if st.button("二人を同じチームにする"): st.session_state.fixed_pairs.append([da, db]); st.success("固定しました")
        for p in st.session_state.fixed_pairs: st.text(f"・{p[0]} & {p[1]}")
        if st.button("固定解除"): st.session_state.fixed_pairs = []; st.rerun()
        if st.button("チーム分け設定へ ➡️", type="primary"): st.session_state.page = "CONFIG"; st.rerun()

# 3. 設定
elif st.session_state.page == "CONFIG":
    st.header("⚙️ チーム分け設定")
    tc = st.radio("試合数:", [1, 2, 3], horizontal=True)
    mode = st.toggle("勝率バランス優先モード", value=True, help="ON: 勝率と分散を均一化。OFF: 前回の味方を避ける。")
    if st.button("チーム生成！", type="primary"):
        act_n = [n for n, p in st.session_state.players.items() if p['active']]
        np_list, pl_prev = [n for n in act_n if n not in st.session_state.last_match_players], [n for n in act_n if n in st.session_state.last_match_players]
        random.shuffle(np_list); random.shuffle(pl_prev); sel = (np_list + pl_prev)[:tc*10]
        st.session_state.last_match_players = sel
        st.session_state.matches = [sys.solve_best_distribution(sel[i*10:(i+1)*10], mode) for i in range(tc)]
        st.session_state.page = "RESULT"; st.rerun()

# 4. 結果入力
elif st.session_state.page == "RESULT":
    st.header("🎮 対戦カード")
    for i, m in enumerate(st.session_state.matches):
        if not m: continue
        st.subheader(f"第 {i+1} 試合")
        c1, c2 = st.columns(2)
        for side, col, style in [("赤チーム", c1, "red-team"), ("白チーム", c2, "white-team")]:
            with col:
                st.markdown(f"<div class='{style}'><h3>{side}</h3>", unsafe_allow_html=True)
                for r, n in m[side].items():
                    pref = " (★)" if st.session_state.players[n]['roles'][0] == r else " (他)"
                    st.write(f"**{r}**: {n}{pref}")
                if st.button(f"{side}勝利", key=f"w_{side}_{i}", disabled=m["done"]):
                    sys.update_omw(i, side); m["done"] = True; st.balloons(); st.session_state.page = "PAIRING"; st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)
    if st.button("最終戦績を確認する"): st.session_state.page = "SUMMARY"; st.rerun()

# 5. 戦績
elif st.session_state.page == "SUMMARY":
    st.header("📊 本日の戦績")
    res_data = []
    for n, p in st.session_state.players.items():
        if p['total'] > 0:
            wr = sys.calculate_win_rate(n)
            res_data.append({"名前": n, "勝率": f"{int(wr*100)}%", "勝": p['wins'], "負": p['total']-p['wins'], "rate": wr})
    if res_data: st.table(pd.DataFrame(res_data).sort_values("rate", ascending=False).drop(columns="rate"))
    
    st.subheader("📋 次回用一括登録テキスト")
    copy_text = "\n".join([f"{n} ({', '.join(p['roles'])})" for n, p in st.session_state.players.items() if p['active']])
    st.text_area("コピーして再利用:", value=copy_text, height=150)
    if st.button("登録画面へ戻る"): st.session_state.page = "REGISTRATION"; st.rerun()