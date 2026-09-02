"""膝関節の内側・外側反力を推定し、3D表示する解析ツール。"""

import numpy as np
import pandas as pd
import tkinter as tk
from tkinter import filedialog
from OpenGL.GL import *
from OpenGL.GLU import *
from OpenGL.GLUT import *
import math
import sys

# --- グローバル変数 ---
window_w, window_h = 800, 600
vbo_id, ebo_id, normal_vbo_id = None, None, None
index_count = 0
calc_results = {}
tibia_matrices = []
knee_forces = []   # 関節反力
knee_moments = []  # 関節モーメント
knee_centers = []  # 膝関節中心
results = []
frame_index = 0
is_animation_paused = False
camera_target = np.array([0.0, 0.0, 0.0])
camera_distance = 1000.0
camera_azimuth = math.radians(0)
camera_elevation = math.radians(15.0)
camera_up_vector = np.array([0.0, 0.0, 1.0])
mouse_state = {}
last_mouse_pos = {'x': 0, 'y': 0}




def select_file(title, filetypes):
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    file = filedialog.askopenfilename(title=title, filetypes=filetypes)

    if not file:
        print(f"ファイルが選択されませんでした:{title}")
        sys.exit()

    return file


def load_knee_force_and_moment(excel_path, side, sample_values):
    """SampleとFieldを照合し、選択側の膝関節反力・モーメントを読む。"""
    sheet_name = "運動力学"
    try:
        df = pd.read_excel(excel_path, sheet_name=sheet_name)
    except ValueError as e:
        raise ValueError(
            f"シート『{sheet_name}』がありません。"
            f"利用可能なシート: {pd.ExcelFile(excel_path).sheet_names}"
        ) from e

    # 平面デジタイズExcelの表記（左/右、L/Rなど）から対象脚を決定する。
    side_text = str(side).strip().upper()
    if "左" in side_text or side_text.startswith("L"):
        side_code = "L"
        side_label = "左"
    elif "右" in side_text or side_text.startswith("R"):
        side_code = "R"
        side_label = "右"
    else:
        raise ValueError(f"解析側を判定できません: {side}")

    force_cols = [f"Force{side_code}KNE:{axis}" for axis in "xyz"]
    moment_cols = [f"Moment{side_code}KNE:{axis}" for axis in "xyz"]
    required_cols = ["Field"] + force_cols + moment_cols
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"運動力学Excelに必要な列がありません: {missing}")

    # RTのSample順に運動力学のFieldを対応させる。
    numeric = df[required_cols].copy()
    numeric[required_cols] = numeric[required_cols].apply(
        pd.to_numeric, errors="coerce"
    )
    numeric = numeric.dropna(subset=["Field"]).drop_duplicates("Field")
    numeric["Field"] = numeric["Field"].astype(int)
    numeric = numeric.set_index("Field").reindex(sample_values)

    # SampleとFieldが一致する全フレームを残す。
    # 反力・モーメントの欠損値は0として扱い、全成分0でもスキップしない。
    data_cols = force_cols + moment_cols
    aligned = numeric[data_cols].fillna(0.0)

    forces = aligned[force_cols].to_numpy(dtype=float).tolist()
    moments = aligned[moment_cols].to_numpy(dtype=float).tolist()
    matched_samples = aligned.index.astype(int).tolist()
    print(
        f"運動力学シート読込完了（{side_label}膝）: {len(forces)} frames\n"
        f"  Sample/Field一致範囲: {matched_samples[0] if matched_samples else '-'}"
        f" ～ {matched_samples[-1] if matched_samples else '-'}\n"
        f"  関節反力: {force_cols}\n"
        f"  モーメント: {moment_cols}"
    )
    return forces, moments, matched_samples

#データ保存用関数
def save_results_to_excel():
    global results

    # データが存在しない場合は終了
    if not results:
        print("保存する計算結果がありません。")
        return

    # DataFrame用のデータリストを作成
    data_list = []
    for result in results:
        if result is None:
            continue
        
        row = {
            "Frame": result.get("frame_no", 0),
            "内側関節反力": result["F_med_scalar"],
            "内側関節反力：X": result["F_med_vec"][0],
            "内側関節反力：Y": result["F_med_vec"][1],
            "内側関節反力：Z": result["F_med_vec"][2],
            "外側関節反力": result["F_lat_scalar"],
            "外側関節反力：X": result["F_lat_vec"][0],
            "外側関節反力：Y": result["F_lat_vec"][1],
            "外側関節反力：Z": result["F_lat_vec"][2],
        }
        data_list.append(row)

    if not data_list:
        print("有効なデータがありません。")
        return

    df_out = pd.DataFrame(data_list)

    # --- 保存先選択ダイアログ ---
    print("保存ダイアログを開きます...")
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True) 
    output_file = filedialog.asksaveasfilename(title="計算結果を保存",defaultextension=".xlsx",filetypes=[("Excel Files", "*.xlsx"), ("All Files", "*.*")],initialfile="結果.xlsx")
    root.destroy()

    if output_file:
        try:
            df_out.to_excel(output_file, index=False)
            print(f"保存完了: {output_file}")
        except Exception as e:
            print(f"保存エラー: {e}")
    else:
        print("保存がキャンセルされました")

def transform_point(m, p):
    re_p = np.append(p, 1.0)
    point_world = m @ re_p
    return point_world[:3]

def transform_vector(m, v):
    """平行移動を加えず、RT行列の回転成分だけでベクトルをWorld座標へ変換する。"""
    return m[:3, :3] @ np.asarray(v, dtype=float)

# --- 3. 計算ロジック (AX=b) ---
def calculate_force_split(frame_idx):
    # インデックスの境界チェック
    if (frame_idx >= len(tibia_matrices)
            or frame_idx >= len(knee_forces)
            or frame_idx >= len(knee_moments)
            or frame_idx >= len(knee_centers)):
        return None

    # 現在のフレームのデータを取得
    current_matrix = tibia_matrices[frame_idx]
    k_center = np.array(knee_centers[frame_idx])
    F_total = np.array(knee_forces[frame_idx])   # Ft: 全関節反力
    M_total = np.array(knee_moments[frame_idx])  # Mt: 全モーメント

    # 内側・外側の接触点（図心）情報を取得
    med_data = calc_results.get("内側", {})
    lat_data = calc_results.get("外側", {})
    if not med_data or not lat_data:
        return None

    p_med_local = med_data.get('centroid')
    p_lat_local = lat_data.get('centroid')

    # 接触点の座標変換 (Local -> World)
    p_med_world = transform_point(current_matrix, p_med_local)
    p_lat_world = transform_point(current_matrix, p_lat_local)

    # 膝中心から接触点への位置ベクトル (モーメントアーム r)
    r_med = p_med_world - k_center
    r_lat = p_lat_world - k_center

    # --- 6x6の行列 A を作成 ---
    # 上段3行: 力のつり合い (Fm + Fl = Ft)
    # 下段3行: モーメントのつり合い (rm x Fm + rl x Fl = Mt)
    
    # 外積を表現する歪対称行列の生成関数
    def skew(r):
        return np.array([
            [0, -r[2], r[1]],
            [r[2], 0, -r[0]],
            [-r[1], r[0], 0]
        ])

    I = np.eye(3)
    A = np.block([
        [I,           I],          # 力のつり合い
        [skew(r_med),  skew(r_lat)]    # モーメントのつり合い
    ])
    
    # 右辺ベクトル b (Ft と Mt を縦に並べる)
    b = np.concatenate([F_total, M_total])

    # 連立方程式 Ax = b を解く
    # ※膝関節の解剖学的配置により行列 A は特異(Rank < 6)になる可能性があるため、擬似逆行列を使用
    try:
        x = np.linalg.pinv(A) @ b
        F_med_vec = x[0:3]
        F_lat_vec = x[3:6]
    except np.linalg.LinAlgError:
        # 解けない場合はゼロまたはエラー処理
        F_med_vec = np.zeros(3)
        F_lat_vec = np.zeros(3)

    # 結果の返却
    return {
        "p_med": p_med_world,
        "p_lat": p_lat_world,
        "k_center": k_center,
        "F_med_vec": -F_med_vec,
        "F_lat_vec": -F_lat_vec,
        "F_med_scalar": np.linalg.norm(F_med_vec),
        "F_lat_scalar": np.linalg.norm(F_lat_vec)
    }


def init_buffers(verts, faces, norms):
    global vbo_id, ebo_id, normal_vbo_id, index_count
    index_count = len(faces) * 3
    vbo_id, normal_vbo_id, ebo_id = glGenBuffers(3)
    glBindBuffer(GL_ARRAY_BUFFER, vbo_id)
    glBufferData(GL_ARRAY_BUFFER, verts.nbytes, verts, GL_STATIC_DRAW)
    glBindBuffer(GL_ARRAY_BUFFER, normal_vbo_id)
    glBufferData(GL_ARRAY_BUFFER, norms.nbytes, norms, GL_STATIC_DRAW)
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, ebo_id)
    glBufferData(GL_ELEMENT_ARRAY_BUFFER, faces.nbytes, faces, GL_STATIC_DRAW)

#OBJ / VBO 処理
def load_obj_vbo(filename):
    verts = []
    faces = []
    try:
        with open(filename, 'r') as f:
            for line in f:
                parts = line.split()
                if not parts:
                    continue
                if parts[0] == '#':
                    continue
                
                # 頂点データ (v x y z)
                if parts[0] == 'v':
                    current_v = []
                    for p in parts[1:4]:
                        current_v.append(float(p))
                    verts.append(current_v)
                
                # 面データ (f v1/vt1/vn1 ...)
                elif parts[0] == 'f':
                    current_f = []
                    for p in parts[1:4]:
                        index_str = p.split('/')[0]
                        current_f.append(int(index_str) - 1)
                    faces.append(current_f)

        verts = np.array(verts, dtype=np.float32)
        faces = np.array(faces, dtype=np.uint32)
        
        # 法線ベクトルの計算
        norm_sum = np.zeros_like(verts)
        v1 = verts[faces[:, 1]] - verts[faces[:, 0]]
        v2 = verts[faces[:, 2]] - verts[faces[:, 0]]
        face_normals = np.cross(v1, v2)
        
        for i in range(len(faces)):
            for j in range(3):
                norm_sum[faces[i, j]] += face_normals[i]
        
        norms = np.zeros_like(norm_sum, dtype=np.float32)
        lengths = np.linalg.norm(norm_sum, axis=1)
        valid = lengths > 0
        norms[valid] = (norm_sum[valid] / lengths[valid, np.newaxis]).astype(np.float32)
        
        return verts, faces, norms
    except Exception as e:
        print(f"OBJ読み込み失敗: {e}")
        return None, None, None

#描画関数
def draw_axes(length=250.0):
    glDisable(GL_LIGHTING)
    glLineWidth(2.0)
    glBegin(GL_LINES)
    # X軸 (赤)
    glColor3f(1, 0, 0); glVertex3f(0, 0, 0); glVertex3f(length, 0, 0)
    # Y軸 (緑)
    glColor3f(0, 1, 0); glVertex3f(0, 0, 0); glVertex3f(0, length, 0)
    # Z軸 (青)
    glColor3f(0, 0, 1); glVertex3f(0, 0, 0); glVertex3f(0, 0, length)
    glEnd()
    glEnable(GL_LIGHTING)

def draw_grid():
    glDisable(GL_LIGHTING)
    glColor3f(0.5, 0.5, 0.5)
    glLineWidth(1.0)
    glBegin(GL_LINES)
    for i in range(-2000, 2100, 100):
        glVertex3f(i, -2000, 0); glVertex3f(i, 2000, 0)
        glVertex3f(-2000, i, 0); glVertex3f(2000, i, 0)
    glEnd()
    glEnable(GL_LIGHTING)

def display():
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
    glLoadIdentity()
    
    pos = get_camera_position()
    gluLookAt(pos[0], pos[1], pos[2], *camera_target, *camera_up_vector)
    
    draw_grid()
    draw_axes()

    # 骨の描画
    if vbo_id is not None:
        glPushMatrix()
        if tibia_matrices: 
            mat = tibia_matrices[frame_index]
            glMultMatrixf(mat.T)
        
        glEnable(GL_LIGHTING)
        glEnable(GL_COLOR_MATERIAL)
        glColor3f(0.7, 0.68, 0.65)
        
        glEnableClientState(GL_VERTEX_ARRAY)
        glEnableClientState(GL_NORMAL_ARRAY)
        glBindBuffer(GL_ARRAY_BUFFER, vbo_id)
        glVertexPointer(3, GL_FLOAT, 0, None)
        glBindBuffer(GL_ARRAY_BUFFER, normal_vbo_id)
        glNormalPointer(GL_FLOAT, 0, None)
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, ebo_id)
        glDrawElements(GL_TRIANGLES, index_count, GL_UNSIGNED_INT, None)
        
        glDisableClientState(GL_NORMAL_ARRAY)
        glDisableClientState(GL_VERTEX_ARRAY)
        glBindBuffer(GL_ARRAY_BUFFER, 0)
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0)
        
        """"""
        # 解析結果(点)
        glDisable(GL_LIGHTING)
        for side, d in calc_results.items():
            glColor3f(1, 0, 0)
            for p in d['points']:
                glPushMatrix()
                glTranslatef(*p)
                glutSolidSphere(1.5, 8, 8)
                glPopMatrix()
            
            # 重心
            glColor3f(0, 0, 1)
            glPushMatrix()
            glTranslatef(*d['centroid'])
            glutSolidSphere(2.5, 8, 8)
            glPopMatrix()
        glPopMatrix()

    # 力の描画 (事前計算済みのリストから取得)
    if results:
        # フレーム番号に対応するデータを検索（インデックスで直接アクセス）
        if 0 <= frame_index < len(results):
            res = results[frame_index]
            
            if res:
                glDisable(GL_LIGHTING)
                """
                # 膝中心
                glColor3f(0, 1, 0)
                glPushMatrix()
                glTranslatef(*res["k_center"])
                glutSolidSphere(3.0, 10, 10)
                glPopMatrix()
                """

                scale = 0.3
                # 内側力 (Magenta)
                if abs(res["F_med_scalar"]) > 0:
                    glColor3f(1, 0, 1)
                    glLineWidth(4.0)
                    glBegin(GL_LINES)
                    glVertex3f(*res["p_med"])
                    glVertex3f(*(res["p_med"] + res["F_med_vec"] * scale))
                    glEnd()
                    
                    glPushMatrix()
                    glTranslatef(*res["p_med"])
                    glutSolidSphere(2.0, 8, 8)
                    glPopMatrix()

                # 外側力 (Cyan)
                if abs(res["F_lat_scalar"]) > 0:
                    glColor3f(0, 1, 1)
                    glLineWidth(4.0)
                    glBegin(GL_LINES)
                    glVertex3f(*res["p_lat"])
                    glVertex3f(*(res["p_lat"] + res["F_lat_vec"] * scale))
                    glEnd()
                    
                    glPushMatrix()
                    glTranslatef(*res["p_lat"])
                    glutSolidSphere(2.0, 8, 8)
                    glPopMatrix()
                
                """
                # リンク線
                glColor3f(1, 1, 1)
                glLineWidth(1.0)
                glBegin(GL_LINES)
                glVertex3f(*res["p_med"])
                glVertex3f(*res["k_center"])
                glVertex3f(*res["p_lat"])
                glVertex3f(*res["k_center"])
                glEnd()
                """

    glutSwapBuffers()

#カメラ操作
def get_camera_position():
    x = camera_target[0] + camera_distance * math.cos(camera_elevation) * math.cos(camera_azimuth)
    y = camera_target[1] + camera_distance * math.cos(camera_elevation) * math.sin(camera_azimuth)
    z = camera_target[2] + camera_distance * math.sin(camera_elevation)
    return np.array([x, y, z])

def rotate_camera(dx, dy):
    global camera_azimuth, camera_elevation
    camera_azimuth -= math.radians(dx * 0.4)
    camera_elevation -= math.radians(dy * 0.4)
    camera_elevation = np.clip(camera_elevation, math.radians(-89.0), math.radians(89.0))

def zoom_camera(dy):
    global camera_distance
    camera_distance = max(10.0, camera_distance + dy * 5.0)

def pan_camera(dx, dy):
    global camera_target
    pos = get_camera_position()
    forward = (camera_target - pos) / np.linalg.norm(camera_target - pos)
    right = np.cross(forward, camera_up_vector) / np.linalg.norm(np.cross(forward, camera_up_vector))
    camera_up = np.cross(right, forward)
    pan_speed = camera_distance * 0.001
    camera_target -= right * dx * pan_speed
    camera_target += camera_up * dy * pan_speed

#コールバック
def timer(v):
    global frame_index
    if not is_animation_paused and tibia_matrices:
        frame_index = (frame_index + 1) % len(tibia_matrices)
    glutPostRedisplay()
    glutTimerFunc(10, timer, 0)

def mouse_button(button, state, x, y):
    global mouse_state, last_mouse_pos
    mouse_state[button] = state
    last_mouse_pos = {'x': x, 'y': y}

def mouse_motion(x, y):
    global last_mouse_pos
    dx, dy = x - last_mouse_pos['x'], y - last_mouse_pos['y']
    if mouse_state.get(GLUT_LEFT_BUTTON) == GLUT_DOWN:
        rotate_camera(dx, dy)
    if mouse_state.get(GLUT_RIGHT_BUTTON) == GLUT_DOWN:
        zoom_camera(dy)
    if mouse_state.get(GLUT_MIDDLE_BUTTON) == GLUT_DOWN:
        pan_camera(dx, dy)
    last_mouse_pos = {'x': x, 'y': y}
    glutPostRedisplay()

def mouse_wheel(wheel, direction, x, y):
    if direction > 0:
        zoom_camera(-2)
    else:
        zoom_camera(2)
    glutPostRedisplay()

def keyboard(key, x, y):
    global is_animation_paused
    if key == b' ':
        is_animation_paused = not is_animation_paused
    glutPostRedisplay()



def main():
    global calc_results, tibia_matrices, knee_forces, knee_moments, knee_centers, results

    #ファイル選択
    print("\n解析に必要なファイルを選択してください")
    p_calc = select_file(title="平面デジタイズエクセル（10点）", filetypes=[("Excel", "*.xlsx")])
    p_motion = select_file(title="歩行データエクセル(RTシート)", filetypes=[("Excel", "*.xlsx")])
    p_kinetics = select_file(title="関節反力・モーメントの運動力学エクセル", filetypes=[("Excel", "*.xlsx")])
    p_obj = select_file(title="脛骨OBJ", filetypes=[("OBJ", "*.obj")])
    print("\n====選択ファイル一覧====")
    print(f"{p_calc}")
    print(f"{p_motion}")
    print(f"{p_kinetics}")
    print(f"{p_obj}")

    #平面デジタイズエクセル読み込み
    ROW_SIDE = 0        # 部位（Side）が記載されている行番号
    ROW_NAME = 4        # データ名（Name）が記載されている行番号
    COL_START = 2       # データが始まる列番号
    COL_STEP = 7        # 次のデータブロックまでの列間隔

    df_calc = pd.read_excel(p_calc, header=None)
    blocks = []
    max_cols = df_calc.shape[1]
    
    num_potential_blocks = (max_cols - 1) // COL_STEP + 1
    for i in range(num_potential_blocks):
        current_col = COL_START + (COL_STEP * i)
        data_name = df_calc.iloc[ROW_NAME, current_col]
        
        if pd.notna(data_name):
            side_name = df_calc.iloc[ROW_SIDE, current_col]
            blocks.append({
                'id': i,
                'name': data_name,
                'side': side_name,
                'c': current_col
            })
            
    print(f"\n===== 解析結果: {len(blocks)}件検出 =====")
    for b in blocks: 
        print(f"[{b['id']}] {b['name']} ({b['side']})")
    
    try:
        sel_idx = int(input("解析対象のIDを入力: "))
        sel = blocks[sel_idx]
    except (ValueError, IndexError):
        print("無効な入力です。終了します。")
        return

    print(f"\n選択: {sel['name']} ({sel['side']})")
    
    for side, r_idx in [("内側", 78), ("外側", 91)]:
        target_cols = [sel['c'], sel['c'] + 1, sel['c'] + 2]
        raw_data = df_calc.iloc[r_idx : r_idx + 10, target_cols]
        pts = raw_data.apply(pd.to_numeric, errors='coerce').dropna().values

        if len(pts) >= 3:
            cent = np.mean(pts, axis=0)
            #法線ベクトルの計算（svd分解）
            _, _, vh = np.linalg.svd(pts - cent)
            norm = vh[2, :]
            # 必要であれば向きを調整
            if norm[2] > 0: norm = norm
            else: norm = -norm
            
            calc_results[side] = {'points': pts, 'normal': norm, 'centroid': cent}
            print(f"【{side}】中心:{cent}")
        else:
            print(f"エラー: {side} の有効なデータ点が不足しています")
            sys.exit()

    #歩行データエクセル読み込み
    df_m = pd.read_excel(p_motion, sheet_name='RT')
    if 'Sample' not in df_m.columns:
        print("RTシートにSample列がありません。")
        return
    df_m['Sample'] = pd.to_numeric(df_m['Sample'], errors='coerce')
    df_m = df_m.dropna(subset=['Sample']).copy()
    df_m['Sample'] = df_m['Sample'].astype(int)

    # 関節反力・モーメントをRTのSampleと運動力学のFieldで照合する。
    try:
        knee_forces, knee_moments, matched_samples = load_knee_force_and_moment(
            p_kinetics, sel['side'], df_m['Sample'].tolist()
        )
    except Exception as e:
        print(f"関節反力・モーメントの読み込みエラー: {e}")
        return
    if not matched_samples:
        print("RTのSampleと運動力学のFieldが一致する有効データがありません。")
        return

    # RTも一致したSampleだけに絞り、同じ順番で座標変換行列を作る。
    df_m = df_m.set_index('Sample').loc[matched_samples].reset_index()
    for _, r in df_m.iterrows():
        mat = np.array([
            [r['WT R1'], r['WT R2'], r['WT R3'], r['WT T1']],
            [r['WT R4'], r['WT R5'], r['WT R6'], r['WT T2']],
            [r['WT R7'], r['WT R8'], r['WT R9'], r['WT T3']],
            [0.0,        0.0,        0.0,        1.0       ]], dtype=np.float32)
        tibia_matrices.append(mat)

    # 運動力学データが脛骨局所座標で記録されているものとして、
    # RTの回転成分を使い、反力・モーメントをWorld座標へそろえる。
    knee_forces = [
        transform_vector(mat, force).tolist()
        for mat, force in zip(tibia_matrices, knee_forces)
    ]
    knee_moments = [
        transform_vector(mat, moment).tolist()
        for mat, moment in zip(tibia_matrices, knee_moments)
    ]
    print("関節反力・モーメントを局所座標からWorld座標へ回転しました。")

    # 膝関節中心は従来どおり、歩行データExcelのDataシートから読み込む。
    try:
        df_d = pd.read_excel(p_motion, sheet_name='Data')
        side_text = str(sel['side']).strip().upper()
        side_code = "L" if ("左" in side_text or side_text.startswith("L")) else "R"
        cols_center = [f'Z{side_code}KNE:{axis}' for axis in 'XYZ']
        missing = [c for c in cols_center if c not in df_d.columns]
        if missing:
            print(f"膝関節中心の列不足: {missing}")
            return
        if 'Field' not in df_d.columns:
            print("DataシートにField列がありません。")
            return
        df_d['Field'] = pd.to_numeric(df_d['Field'], errors='coerce')
        df_d = df_d.dropna(subset=['Field']).copy()
        df_d['Field'] = df_d['Field'].astype(int)
        centers = df_d.set_index('Field').reindex(matched_samples)[cols_center]
        centers = centers.apply(pd.to_numeric, errors='coerce')
        if centers.isna().any(axis=None):
            bad_samples = centers.index[centers.isna().any(axis=1)].tolist()
            print(f"膝関節中心がないFieldがあります: {bad_samples[:10]}")
            return
        knee_centers = centers.to_numpy(dtype=float).tolist()
        print(f"膝関節中心読込完了: {len(knee_centers)} frames")
    except Exception as e:
        print(f"膝関節中心の読み込みエラー: {e}")
        return

    print("\n全フレームの解析を実行中...")
    num_frames = min(
        len(tibia_matrices), len(knee_forces),
        len(knee_moments), len(knee_centers)
    )
    if num_frames == 0:
        print("解析できる共通フレームがありません。")
        return
    if len({len(tibia_matrices), len(knee_forces), len(knee_moments), len(knee_centers)}) > 1:
        print(
            "注意: データ数が異なるため、最短のフレーム数に合わせます。 "
            f"RT={len(tibia_matrices)}, 関節反力={len(knee_forces)}, "
            f"モーメント={len(knee_moments)}, 膝中心={len(knee_centers)}"
        )
    for i in range(num_frames):
            result = calculate_force_split(i)
            if result is not None:
                result["frame_no"] = matched_samples[i]
            results.append(result)

    print(f"計算完了: {num_frames} フレーム処理")

    save_results_to_excel()

    # OpenGL初期化
    glutInit()
    glutInitDisplayMode(GLUT_DOUBLE | GLUT_RGB | GLUT_DEPTH)
    glutInitWindowSize(window_w, window_h)
    glutCreateWindow("結果".encode('shift_jis')) 
    glEnable(GL_DEPTH_TEST)
    glEnable(GL_LIGHTING)
    glEnable(GL_LIGHT0)
    glClearColor(0.1, 0.1, 0.1, 1)
    glLightfv(GL_LIGHT0, GL_POSITION, [0, 500, 1000, 0])
    
    v, f, n = load_obj_vbo(p_obj)
    if v is not None: 
        init_buffers(v, f, n)
    glMatrixMode(GL_PROJECTION)
    gluPerspective(45, window_w/window_h, 10, 10000)
    glMatrixMode(GL_MODELVIEW)
    
    glutDisplayFunc(display)
    glutTimerFunc(10, timer, 0)
    glutMouseFunc(mouse_button)
    glutMotionFunc(mouse_motion)
    glutMouseWheelFunc(mouse_wheel)
    glutKeyboardFunc(keyboard)
    glutMainLoop()

if __name__ == "__main__":
    main()
