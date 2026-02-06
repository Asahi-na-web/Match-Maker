import streamlit as st
import pandas as pd
import json
import random
import gspread
from google.oauth2.service_account import Credentials
from itertools import combinations, permutations

# --- 設定 ---
ROLES = ["上キャ", "上学習", "中央", "下キャ", "下学習"]

# --- Google Sheets 接続設定 ---
def get_gspread_client():
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.secrets["gcp_service_account"])
    
    # 【重要】改行コードのエラー対策
    # Secretsから読み込んだ際に \\n になってしまう現象を防止
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
            # 安全にJSONをパースする処理
            try:
                roles_list = json.loads(r['roles'])
            except:
                roles_list = [ROLES[2]] # 失敗時は中央をデフォルトに
                
            data[r['name']] = {
                'active': bool(r['active']),
                'wins': int(r['wins']),
                'total': int(r['total']),
                'omw': float(r['omw']),
                'last_teammates': json.loads(r['last_teammates']) if r['last_teammates'] else [],
                'opponents': json.loads(r['opponents']) if r['opponents'] else [],
                'roles': roles_list
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
            rows.append([
                n, int(p['active']), p['wins'], p['total'], p['omw'],
                json.dumps(p['last_teammates'], ensure_ascii=False),
                json.dumps(p['opponents'], ensure_ascii=False),
                json.dumps(p['roles'], ensure_ascii=False)
            ])
        sheet.update('A1', rows)
    except Exception as e:
        st.error(f"データ保存エラー: {e}")

# --- ロジック（仕組み・ボタン・機能を維持） ---
class ProfessionalTeamSystem:
    def __init__(self):
        if 'players' not in st.session_state:
            st.session_state.players = load_from_sheets()
            st.session_state.fixed_pairs = []
            st.session_state.last_match_players = []
            st.session_state.matches = []
            st.session_state.omw_balance_mode = False
            st.session_state.page = "REGISTRATION"

    def calculate_win_rate(self, name):
        p = st.session_state.players[name]
        return max(0.333, p['wins'] / p['total'] if p['total'] > 0 else 0)

    def get_current_omw(self, name):
        p = st.session_state.players[name]
        if not p['opponents']: return 0.333
        return sum(self.calculate_win_rate(n) for n in p['opponents']) / len(p['opponents'])

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
                p['omw'] = self.get_current_omw(name)
        save_to_sheets(st.session_state.players)

    def solve_best_distribution(self, names):
        pool = list(combinations(names, 5)); random.shuffle(pool); cands = []; fallback_cands = []
        for ta in pool:
            tb = [n for n in names if n not in ta]; pf = False
            for p in st.session_state.fixed_pairs:
                if (p[0] in ta and p[1] not in ta) or (p[0] in tb and p[1] not in tb): pf = True; break
            ra, wa = self.assign_roles_flexible(ta); rb, wb = self.assign_roles_flexible(tb); rep = 0
            for n in ta: rep += len(set(st.session_state.players[n].get('last_teammates', [])) & set(ta))
            d = {"赤チーム": ra, "白チーム": rb, "warn": (wa or wb or pf), "rep": rep, "done": False}
            if st.session_state.omw_balance_mode:
                wa_list = [self.calculate_win_rate(n) for n in ta]; wb_list = [self.calculate_win_rate(n) for n in tb]
                d["diff"] = abs(sum(wa_list) - sum(wb_list)); d["var"] = abs(pd.Series(wa_list).std() - pd.Series(wb_list).std())
            if not d["warn"] and rep <= 2: cands.append(d)
            else: fallback_cands.append(d)
            if len(cands) > 50 or len(fallback_cands) > 200: break
        res_list = cands if cands else fallback_cands
        return min(res_list, key=lambda x: (x.get("diff", 0) if st.session_state.omw_balance_mode else 0, x.get("var", 0) if st.session_state.omw_balance_mode else 0, x["warn"], x["rep"])) if res_list else None

    def assign_roles_flexible(self, members):
        for p in permutations(members):
            t = {}
            for i, r in enumerate(ROLES):
                if r in st.session_state.players[p[i]]['roles']: t[r] = p[i]
                else: break
            if len(t) == 5: return t, False
        return {ROLES[i]: members[i] for i in range(5)}, True

sys = ProfessionalTeamSystem()

# --- UI (REGISTRATION画面の改善) ---
if st.session_state.page == "REGISTRATION":
    st.title("【プレイヤー管理】")
    
    # 登録フォーム
    with st.expander("新規登録 / ロール更新", expanded=True):
        with st.form("add_player", clear_on_submit=True):
            ni = st.text_input("名前（既存名入力でロール更新）:")
            rm = st.selectbox("第１ロール:", ROLES)
            rc = st.multiselect("他対応可能ロール:", ROLES) # 全てのロールから選択可能に変更
            if st.form_submit_button("登録/更新"):
                if ni:
                    # 重複を排除して結合
                    sel_r = list(dict.fromkeys([rm] + rc))
                    if ni in st.session_state.players:
                        st.session_state.players[ni]['roles'] = sel_r
                    else:
                        st.session_state.players[ni] = {'roles': sel_r, 'active': True, 'wins': 0, 'total': 0, 'omw': 0.0, 'last_teammates': [], 'opponents': []}
                    save_to_sheets(st.session_state.players); st.rerun()

    if st.button("全員戦績リセット", type="secondary"):
        for p in st.session_state.players.values(): p.update({'wins':0,'total':0,'omw':0.0,'last_teammates':[],'opponents':[]})
        save_to_sheets(st.session_state.players); st.rerun()

    st.subheader("プレイヤー一覧")
    for n, p in list(st.session_state.players.items()):
        col1, col2 = st.columns([3, 1])
        # ロールを表示するように改善
        roles_disp = ",".join(p['roles'])
        c = col1.checkbox(f"{n} [{roles_disp}] ({p['total']}戦)", value=p['active'], key=f"check_{n}")
        if c != p['active']: 
            st.session_state.players[n]['active'] = c; save_to_sheets(st.session_state.players)
        if col2.button("削", key=f"del_{n}"):
            del st.session_state.players[n]; save_to_sheets(st.session_state.players); st.rerun()

    if st.button("次へ (ペア設定)", type="primary"):
        st.session_state.page = "PAIRING"; st.rerun()

# --- 以降の画面 (PAIRING, CONFIG, RESULT, SUMMARY) も維持 ---
# （前回コードと同様のため、ここでは変更点に集中します）
elif st.session_state.page == "PAIRING":
    st.title("【ペア設定】")
    pl = sorted([n for n, p in st.session_state.players.items() if p['active']])
    if len(pl) < 2:
        st.warning("人数不足"); st.button("戻る", on_click=lambda: setattr(st.session_state, 'page', "REGISTRATION"))
    else:
        da = st.selectbox("ペア1", pl); db = st.selectbox("ペア2", pl)
        if st.button("固定"): st.session_state.fixed_pairs.append([da, db]); st.success(f"固定: {da}&{db}")
        if st.button("解除"): st.session_state.fixed_pairs = []; st.info("解除完了")
        if st.button("次へ (試合設定)"): st.session_state.page = "CONFIG"; st.rerun()

elif st.session_state.page == "CONFIG":
    st.title("【設定】")
    tc = st.selectbox("試合数:", [1, 2, 3])
    mode = st.selectbox("モード:", [("通常", False), ("勝率バランス重視", True)], format_func=lambda x: x[0])
    if st.button("開始"):
        st.session_state.omw_balance_mode = mode[1]
        act_n = [n for n, p in st.session_state.players.items() if p['active']]
        if len(act_n) < tc * 10: st.error(f"人数不足(現在{len(act_n)}人)"); st.stop()
        np = [n for n in act_n if n not in st.session_state.last_match_players]
        pl_prev = [n for n in act_n if n in st.session_state.last_match_players]
        random.shuffle(np); random.shuffle(pl_prev); sel = (np + pl_prev)[:tc*10]
        st.session_state.last_match_players = sel
        st.session_state.matches = []
        for i in range(tc):
            res = sys.solve_best_distribution(sel[i*10:(i+1)*10])
            st.session_state.matches.append(res)
        st.session_state.page = "RESULT"; st.rerun()

elif st.session_state.page == "RESULT":
    st.title("【対戦カード】")
    for i, m in enumerate(st.session_state.matches):
        st.markdown(f"### --- 第{i+1}試合 ---")
        if m:
            cols = st.columns(2)
            for idx, side in enumerate(["赤チーム", "白チーム"]):
                with cols[idx]:
                    st.write(f"**【{side}】**{' ⚠️' if m['warn'] else ''}")
                    for r, n in m[side].items(): st.text(f"{r}: {n}")
                    if st.button(f"{side}勝利", key=f"win_{i}_{side}", disabled=m.get("done", False)):
                        sys.update_omw(i, side); m["done"] = True; st.rerun()
        else: st.error("生成エラー")
    if st.button("終了・集計"): st.session_state.page = "SUMMARY"; st.rerun()

elif st.session_state.page == "SUMMARY":
    st.title("最終戦績")
    d = [{"名前": n, "試合数": p['total'], "勝率": f"{int(sys.calculate_win_rate(n)*100)}%", "OMW%": round(sys.get_current_omw(n), 4)} for n, p in st.session_state.players.items() if p['total'] > 0]
    if d: st.table(pd.DataFrame(d).sort_values(by="勝率", ascending=False))
    if st.button("トップに戻る"): st.session_state.page = "REGISTRATION"; st.rerun()