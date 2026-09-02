# Knee Joint Force Analysis

歩行解析データから、膝関節の合力・モーメントを内側および外側の接触力へ分配し、結果をExcelに保存して3D表示するPythonデスクトップアプリです。

## 主な機能

- 平面デジタイズデータから内側・外側接触面の図心と法線を算出
- RTシートの `Sample` と運動力学シートの `Field` を照合
- 脛骨局所座標の関節反力・モーメントをWorld座標へ変換
- 力とモーメントのつり合い式を擬似逆行列で解き、内側・外側反力を推定
- 計算結果をExcelファイルへ保存
- OBJモデル上で反力ベクトルをアニメーション表示

## 必要環境

- Python 3.10以上
- OpenGL / GLUTが利用できるデスクトップ環境

## セットアップ

```bash
git clone <このリポジトリのURL>
cd knee-joint-force-analysis
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

macOS / Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

環境によってはFreeGLUTを別途インストールする必要があります。

## 使用方法

起動後、ダイアログの案内に従って次の4ファイルを選択します。

1. 平面デジタイズExcel（10点）
2. 歩行データExcel（`RT`・`Data`シート）
3. 関節反力・モーメントExcel（`運動力学`シート）
4. 脛骨のOBJモデル

続いてターミナルに表示される解析対象IDを入力します。計算後、保存ダイアログで結果Excelの保存先を指定すると、3D表示が始まります。

### 3D画面の操作

- マウス操作：回転・ズーム・平行移動
- Space：アニメーションの一時停止 / 再開

## 入力データの主な条件

- `RT` シート：`Sample`、`WT R1`〜`WT R9`、`WT T1`〜`WT T3`
- `Data` シート：`Field`、`ZLKNE:X/Y/Z` または `ZRKNE:X/Y/Z`
- `運動力学` シート：`Field`、左右膝のForce・Moment各3成分
- OBJ：三角形面で構成された脛骨モデル

列名やシート名はコード内の想定と完全に一致する必要があります。

## 出力

各フレームについて、内側・外側関節反力の大きさとXYZ成分を `.xlsx` 形式で保存します。

## 注意事項

- 本ツールは研究・教育目的の試作ソフトウェアです。
- 医療診断や治療判断には使用しないでください。
- 個人情報を含むExcelや3DモデルをGitHubへアップロードしないでください。
- 入力値の単位および座標系が揃っていることを事前に確認してください。

## ライセンス

現時点ではライセンスを設定していません。第三者による複製・改変・再配布を許可する場合は、用途に合うライセンスを追加してください。
