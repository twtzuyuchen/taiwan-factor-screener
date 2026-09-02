# 台股五因子選股器 Taiwan 5-Factor Stock Screener

依據**價值 (Value)、質量 (Quality)、動能 (Momentum)、規模 (Size)、低波動 (Low Volatility)** 五大因子，對台股上市（TWSE）與上櫃（TPEx）股票計算 Z-Score 綜合評分並排名。

> ⚠️ 本工具僅供研究與教育用途，不構成投資建議。因子評分是橫斷面統計方法，不保證未來績效。

## 架構：像「台股儀表板」一樣，資料先算好、網頁只負責顯示

這個工具分成兩半，職責完全分開：

1. **`scripts/build_factors.py`** — 在 **GitHub Actions（伺服器端）** 執行，不是在瀏覽器執行：
   - 向 TWSE / TPEx 官方 Open API 取得全市場最新快照（免金鑰）
   - 依成交金額篩出流動性前 N 名
   - 逐檔向 FinMind API 查詢歷史股價、市值、財報、資產負債表（帶 `data_id`，免費方案可用）
   - 把每檔股票的原始數值寫成 `data/factors_latest.json`
2. **`index.html`** — 純靜態網頁，**只讀取** `data/factors_latest.json`，在瀏覽器裡用 JavaScript 即時計算 Z-Score、依你調整的權重滑桿算加權分數、排序、篩選。不會呼叫任何外部 API。

這樣設計解決了兩個純前端版本會遇到的問題：

- **CORS**：伺服器對伺服器的請求不受瀏覽器同源政策限制；瀏覽器直接呼叫 TWSE/TPEx 官方 API 實測會被擋下（CORS 不開放），改成 GitHub Actions 執行就沒有這個問題。
- **等待時間 / 速率限制**：資料是排程算好的，打開網頁就直接看到結果，不用每次等好幾分鐘逐檔抓取；FinMind 的請求也分散在排程執行的時候，不會卡在你使用網頁的當下。

代價：資料不是「即時」的，是照排程更新（預設平日收盤後跑一次），如果想要更新更頻繁，需要調整排程或手動觸發 workflow。

---

## 設定步驟

### 1. 上傳到你自己的 GitHub repo

把整個資料夾（含 `index.html`、`scripts/`、`.github/workflows/`、`data/`、`requirements.txt`）上傳到一個新的 **Public** repo（Actions + 免費 GitHub Pages 都需要 Public repo，且程式碼裡沒有放任何機密，公開沒問題）。

### 2. 申請 FinMind Token，設成 GitHub Secret（不是貼在網頁裡）

1. 到 [FinMind 官網](https://finmindtrade.com/) 免費註冊帳號，在會員頁面取得 token。
2. 到你的 repo → **Settings → Secrets and variables → Actions → New repository secret**。
3. Name 填 `FINMIND_TOKEN`，Value 貼上你的 token，儲存。

這樣 token 只存在 GitHub 的加密機密庫裡，不會出現在原始碼、網頁、或任何人看得到的地方；`.github/workflows/update-factors.yml` 會在執行時透過 `secrets.FINMIND_TOKEN` 讀取它。

### 3. 開啟 GitHub Actions 排程

Actions 預設是開的，不需要額外設定。`.github/workflows/update-factors.yml` 裡已經排好：

- **自動排程**：平日台北時間 15:00（收盤後）自動執行一次，抓最新快照、算好分數、把 `data/factors_latest.json` commit 回 repo。
- **手動觸發**：到 repo 的 **Actions** 分頁 → 左側選「Update factor data」→ 右上角「Run workflow」，可以自訂：
  - 選股池大小（預設 200）
  - 動能回看季數（預設 2 季，約 126 個交易日；可填小數，例如 0.5）
  - 波動度回看季數（預設 1 季，約 63 個交易日；可填小數）
  - 市場範圍（上市+上櫃 / 僅上市 / 僅上櫃）

第一次設定完，建議先手動觸發跑一次，確認能成功產生資料，不用等到排程時間。

### 4. 開啟 GitHub Pages

Repo 頁面 → **Settings → Pages** → Source 選「Deploy from a branch」→ Branch 選 `main`、資料夾選 `/ (root)` → Save。等 1-2 分鐘，會出現網址 `https://<你的帳號>.github.io/<repo名稱>/`。

之後每次 Actions 排程更新 `data/factors_latest.json`（等於對 `main` 分支的一次 commit），GitHub Pages 會自動重新部署，網頁就會顯示最新資料。

---

## 使用方式

打開部署好的網址：

- 上方狀態列會顯示資料筆數、選股池大小、市場範圍、**資料更新時間**。
- 調整「因子權重」滑桿或「排除」勾選框，排名表格會**立即**重新計算（不需要重新整理頁面、不需要重新抓資料），因為原始數值都已經在 `data/factors_latest.json` 裡了。
- 點欄名可以排序，右上角「⬇ 匯出 CSV」可以把目前排名存成檔案。

如果狀態列顯示「資料載入失敗」，代表 `data/factors_latest.json` 還沒被 Actions 產生過（見上面「第一次設定完，建議先手動觸發跑一次」），或是你用 `file://` 直接雙擊打開 `index.html`（瀏覽器會擋掉本機 JSON 讀取）——用 GitHub Pages 網址開，或本機執行 `python -m http.server` 起一個簡易伺服器再開。

---

## 五大因子計算方式

| 因子 | 資料來源 | 計算邏輯 | 方向 |
|---|---|---|---|
| 價值 Value | TWSE `BWIBBU_d` / TPEx `tpex_mainboard_peratio_analysis`（官方全市場快照，PER、PBR） | 對 PER、PBR 做 Z-Score 後取負號平均 | 越低越好 |
| 質量 Quality | FinMind EPS（`TaiwanStockFinancialStatements`）＋ 資產負債表（`TaiwanStockBalanceSheet`）＋ PBR 反推每股淨值 | ROE（近四季 EPS 加總／每股淨值）越高越好；負債比（負債／資產）越低越好；EPS 波動係數（近8季標準差／平均）越低越好 | 綜合 |
| 動能 Momentum | FinMind 歷史股價（`TaiwanStockPrice`） | 過去 N 季累積報酬率（N 可在觸發 workflow 時調整，預設2季≈126個交易日） | 越高越好 |
| 規模 Size | FinMind `TaiwanStockMarketValue`（查不到則自動改用「收盤價 × 成交金額」流動性代理值，表格中標示 ≈） | 市值 Z-Score 取負號 | 越小越好 |
| 低波動 Low Volatility | FinMind 歷史股價日報酬 | 年化標準差（√252 年化） | 越低越好 |

綜合分數 = 五個因子 Z-Score 依你在網頁上調整的權重加權平均。權重總和不需要等於 100，會自動正規化。**這一步全部在瀏覽器端即時計算**，`build_factors.py` 只負責準備原始數值，不做加權。

---

## 已知限制 / 你可能要注意的事

- **資料不是即時的**：TWSE/TPEx 官方 API 本身就只提供「最新一個交易日」的快照、沒有回溯查詢，加上 GitHub Actions 是排程執行，所以你看到的永遠是「上次排程執行當下」的最新收盤資料，不是你打開網頁那一刻的即時報價。
- **排程時間可能需要微調**：預設抓平日台北時間 15:00，如果那個時間點 TWSE/TPEx 官方資料還沒更新完成（例如遇到特殊延遲），可能抓到前一個交易日的資料。可以自行調整 `.github/workflows/update-factors.yml` 裡的 cron 時間，或改成手動觸發。
- **欄位名稱可能對不上**：TWSE/TPEx 官方 API 與 FinMind 資料集的實際 JSON 欄位名稱，是我依官方文件描述推斷、無法在建置環境即時連線測試確認，`scripts/build_factors.py` 開頭的 `TWSE_FIELDS` / `TPEX_FIELDS` 針對每個欄位都列了幾個候選名稱去嘗試比對，但仍可能有出入。如果 Actions 執行完 `data/factors_latest.json` 裡 `row_count` 是 0 或數字明顯不對，去 repo 的 **Actions** 分頁點進那次執行紀錄看 log（腳本會印出每一步的筆數與警告），照 log 訊息調整對應的欄位名稱。
- **FinMind 速率限制**：免費方案仍有每小時請求數限制。選股池開太大（例如全市場 1700+ 檔）可能導致單次 workflow 執行時間拉長或中途被限速；`build_factors.py` 有內建重試與延遲（`--delay`，預設 0.35 秒/次），必要時可以調高。GitHub Actions 單次 job 預設有執行時間上限（免費方案通常是 6 小時），對這個工具來說通常不是問題，但選股池數百檔以上時，建議先用手動觸發測試一次完整跑完的時間。
- **規模因子的市值資料**：`TaiwanStockMarketValue` 查不到時會自動退回「收盤價 × 成交金額」的流動性代理值（不是真的市值），表格中會用 `≈` 標示。
- **動能因子波動大**：如同因子投資的原則，動能因子本身波動劇烈、反轉時容易「咬人」，不建議把動能權重拉到極端值單獨使用，建議搭配質量或低波動因子平滑風險。
- **這不是回測工具**：本工具只做「單一時點的橫斷面排名」，不驗證因子策略的歷史績效。若要驗證某個因子組合長期是否有效，需要另外做時間序列回測。
- **公開 repo 的資料是公開的**：因為用免費 GitHub Pages，`data/factors_latest.json`（選股結果）任何人都能存取，但這只是公開市場資料的排名，不含任何個資或機密，通常沒問題；如果你在意，可以改用 GitHub 付費方案的 Private repo + Pages，或改把資料存到別處。

---

## 檔案結構

```
taiwan-factor-screener/
├── index.html                          ← 靜態網頁，只讀 data/factors_latest.json
├── data/
│   └── factors_latest.json             ← GitHub Actions 產生的選股結果（含佔位初始值）
├── scripts/
│   └── build_factors.py                ← 伺服器端執行的資料建置腳本
├── .github/workflows/
│   └── update-factors.yml              ← 排程 + 手動觸發的 GitHub Actions workflow
├── requirements.txt                    ← Python 依賴（只有 requests）
└── README.md
```

## 本機測試（不透過 GitHub）

```bash
# 1. 產生資料（需要 FinMind token）
export FINMIND_TOKEN=你的token
python scripts/build_factors.py --universe-size 50   # 先用小選股池測試

# 2. 起一個簡易伺服器預覽網頁（不能直接雙擊 index.html，瀏覽器會擋本機 JSON 讀取）
python -m http.server 8000
# 打開 http://localhost:8000
```

## License

MIT — 自由使用、修改、散布。
