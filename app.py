import streamlit as st
import pandas as pd
import json
import random
import gspread
import re
from google.oauth2.service_account import Credentials
from itertools import combinations, permutations

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

# --- Google Sheets 接続設定（キャッシュ化） ---
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
            try:
                roles_list = json.loads(r['roles']) if r['roles'] else ROLES.copy()
            except:
                roles_list = ROLES.copy()
            data[r['name']] = {
                'active': bool(r['active']),
                'wins': int(r['wins'] or 0),
                'total': int(r['total'] or 0),
                'omw': float(r['omw'] or 0.0),
                'last_teammates': json.loads(r['last_teammates']) if r.get('last_teammates') else [],
                'opponents': json.loads(r['opponents']) if r.get('opponents') else [],
                'roles': roles_list
            }
        return data
    except Exception as e:
        st.error(f"データ読み込みエラー: {e}")
        return {}

def save_to_sheets(players):
    try:
        client = get_gspread_client()
        if not client: return
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
        st.error(f"保存エラー: {e}")

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
        p = st.session_state.players.get(name)
        if not p or p['total'] == 0: return 0
        return p['wins'] / p['total']

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
    st.info(f"工程: {st.session_state.page}")
    if st.button("データ強制同期"):
        st.cache_resource.clear()
        st.session_state.players = load_from_sheets()
        st.rerun()
    if st.button("最初からやり直す", type="secondary"):
        st.session_state.page = "REGISTRATION"
        st.rerun()

# 1. 登録画面
if st.session_state.page == "REGISTRATION":
    st.header("プレイヤー管理")
    col_f1, col_f2 = st.columns([1, 1])
    
    with col_f1:
        st.subheader("個別登録・更新")
        with st.form("add_p", clear_on_submit=True):
            ni = st.text_input("プレイヤー名:")
            rm = st.selectbox("メインロール", ROLES)
            rc = st.multiselect("サブロール", ROLES, default=[rm])
            if st.form_submit_button("登録・更新"):
                if ni:
                    st.session_state.players[ni] = {'roles': list(set([rm]+rc)), 'active': True, 'wins': 0, 'total': 0, 'omw': 0.0, 'last_teammates': [], 'opponents': []}
                    save_to_sheets(st.session_state.players); st.success(f"{ni} 登録完了"); st.rerun()

    with col_f2:
        st.subheader("テキスト一括登録（名前とロール）")
        bulk_text = st.text_area("「名前 (ロール1,ロール2)」の形式でペースト:", placeholder="プレイヤーA (上キャ, 中央)\nプレイヤーB (下学習)", height=150)
        if st.button("一括適用"):
            if bulk_text:
                lines = bulk_text.split('\n')
                count = 0
                for line in lines:
                    if "(" in line and ")" in line:
                        # 「名前 (ロール1, ロール2)」形式の解析
                        name_part = line.split("(")[0].strip()
                        roles_part = line.split("(")[1].split(")")[0].strip()
                        extracted_roles = [r.strip() for r in roles_part.split(",") if r.strip() in ROLES]
                        
                        if name_part:
                            if name_part not in st.session_state.players:
                                st.session_state.players[name_part] = {'wins':0, 'total':0, 'omw':0.0, 'last_teammates':[], 'opponents':[]}
                            st.session_state.players[name_part].update({
                                'roles': extracted_roles if extracted_roles else ROLES.copy(),
                                'active': True
                            })
                            count += 1
                if count > 0:
                    save_to_sheets(st.session_state.players); st.success(f"{count}名を更新しました"); st.rerun()

        if st.button("全戦績をリセット"):
            for p in st.session_state.players.values(): p.update({'wins':0,'total':0,'last_teammates':[],'opponents':[]})
            save_to_sheets(st.session_state.players); st.rerun()

    st.divider()
    active_count = sum(1 for p in st.session_state.players.values() if p['active'])
    st.subheader(f"参加名簿 ({active_count}名 選択中)")
    cols = st.columns(3)
    sorted_players = dict(sorted(st.session_state.players.items()))
    for i, (n, p) in enumerate(sorted_players.items()):
        with cols[i % 3]:
            st.markdown(f"<div class='player-card'>", unsafe_allow_html=True)
            c = st.checkbox(f"**{n}** ({','.join(p.get('roles', []))})", value=p['active'], key=f"c_{n}")
            if c != p['active']:
                st.session_state.players[n]['active'] = c
                save_to_sheets(st.session_state.players)
            st.markdown("</div>", unsafe_allow_html=True)

    if st.button("ペア設定へ進む", type="primary"):
        st.session_state.page = "PAIRING"; st.rerun()

# 2. ペア設定
elif st.session_state.page == "PAIRING":
    st.header("ペア固定設定")
    pl = sorted([n for n, p in st.session_state.players.items() if p['active']])
    if len(pl) < 10:
        st.warning(f"10名必要です（現在{len(pl)}名）。登録画面でチェックを入れてください。")
        if st.button("戻る"): st.session_state.page = "REGISTRATION"; st.rerun()
    else:
        c1, c2 = st.columns(2)
        da, db = c1.selectbox("プレイヤーA", pl), c2.selectbox("プレイヤーB", pl)
        if st.button("この二人を同じチームにする"):
            st.session_state.fixed_pairs.append([da, db]); st.success(f"固定: {da} & {db}")
        if st.session_state.fixed_pairs:
            for p in st.session_state.fixed_pairs: st.text(f"・{p[0]} & {p[1]}")
            if st.button("固定解除"): st.session_state.fixed_pairs = []; st.rerun()
        if st.button("チーム分け設定へ ", type="primary"):
            st.session_state.page = "CONFIG"; st.rerun()

# 3. 設定
elif st.session_state.page == "CONFIG":
    st.header("チーム分け設定")
    tc = st.radio("試合数:", [1, 2, 3], horizontal=True)
    mode = st.toggle("勝率バランス考慮", value=True)
    if st.button("チーム生成！", type="primary"):
        act_n = [n for n, p in st.session_state.players.items() if p['active']]
        np, pl_prev = [n for n in act_n if n not in st.session_state.last_match_players], [n for n in act_n if n in st.session_state.last_match_players]
        random.shuffle(np); random.shuffle(pl_prev); sel = (np + pl_prev)[:tc*10]
        st.session_state.last_match_players = sel
        st.session_state.matches = [sys.solve_best_distribution(sel[i*10:(i+1)*10], mode) for i in range(tc)]
        st.session_state.page = "RESULT"; st.rerun()

# 4. 結果入力
elif st.session_state.page == "RESULT":
    st.header(" 対戦カード & 結果入力")
    for i, m in enumerate(st.session_state.matches):
        if not m: continue
        st.subheader(f"第 {i+1} 試合")
        col_r, col_w = st.columns(2)
        with col_r:
            st.markdown(f"<div class='red-team'><h3>赤 {'⚠️' if m['warn'] else ''}</h3>", unsafe_allow_html=True)
            for r, n in m["赤チーム"].items(): st.write(f"**{r}**: {n}")
            if st.button(f"赤勝利", key=f"win_r_{i}", disabled=m["done"]):
                sys.update_omw(i, "赤チーム"); m["done"] = True; st.balloons(); st.session_state.page = "PAIRING"; st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
        with col_w:
            st.markdown(f"<div class='white-team'><h3>白</h3>", unsafe_allow_html=True)
            for r, n in m["白チーム"].items(): st.write(f"**{r}**: {n}")
            if st.button(f"白勝利", key=f"win_w_{i}", disabled=m["done"]):
                sys.update_omw(i, "白チーム"); m["done"] = True; st.balloons(); st.session_state.page = "PAIRING"; st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
    if st.button("最終戦績を確認する"): st.session_state.page = "SUMMARY"; st.rerun()

# 5. 戦績（表示項目を限定 & コピー用エリア追加）
elif st.session_state.page == "SUMMARY":
    st.header("本日の戦績ランキング")
    data_list, copy_list = [], []
    for n, p in sorted(st.session_state.players.items()):
        if p['total'] > 0:
            wr = sys.calculate_win_rate(n)
            data_list.append({"名前": n, "勝率数値": wr, "勝率": f"{int(wr*100)}%", "勝ち": p['wins'], "負け": p['total'] - p['wins'], "OMW%": f"{p['omw']:.2f}%"})
        if p['active']:
            copy_list.append(f"{n} ({', '.join(p['roles'])})")
    
    if data_list:
        st.table(pd.DataFrame(data_list).sort_values(by="勝率数値", ascending=False).drop(columns=["勝率数値"]))
    else:
        st.info("試合データがありません。")

    st.divider()
    st.subheader("次回用コピーテキスト ")
    st.text_area("登録画面の「一括登録」にペーストできます:", value="\n".join(copy_list), height=150)

    if st.button("登録画面に戻る"): st.session_state.page = "REGISTRATION"; st.rerun()