# 日本省钱情报局 / Japan Deals CN

零依赖静态网站 + Python 采集候选队列（不含 Telegram）。默认 **0 条商品**，不虚构折扣。

## 本地预览

```bash
python scripts/build_feed.py
cd site && python -m http.server 8000
```
打开 http://localhost:8000 。

## 上线 GitHub Pages

1. 建立公开 GitHub 仓库，把本项目文件推送到 `main`（包括 `.github` 隐藏目录）。
2. Settings → Pages → Build and deployment → Source 选择 **GitHub Actions**。
3. Actions → `Build and publish verified deals` → Run workflow。
4. 部署后地址通常是 `https://<用户名>.github.io/<仓库名>/`。具体以 GitHub Pages 页面显示为准。
5. 正式公开前补全联系邮箱、隐私政策、运营主体与联盟披露。

## 如何发布真实优惠

复制 `data/manual_example.json` 的对象到 `data/manual_deals.json` 数组，替换所有占位字段；先人工核实商品链接、现价、适用条件和有效期，然后将 `verified` 改为 `true`。提交至 main 后自动构建。**不要将未核实商品标为 true**。

若有原价证据，可额外填写 `reference_price_jpy` 与 `reference_price_evidence_url`，否则网站不显示原价。当前无价格历史追踪，不能宣称历史最低。

## 楽天 API（可选）

在 GitHub Settings → Secrets and variables → Actions 添加 `RAKUTEN_APP_ID`、`RAKUTEN_ACCESS_KEY`，可选 `RAKUTEN_AFFILIATE_ID`。工作流把查询结果放入 `data/rakuten_candidates.json` **候选队列**，不会自动当成真实优惠发布。正式接入前请按 https://webservice.rakuten.co.jp/documentation/ichiba-item-search 核对接口和使用条款。接口失败会打印简要错误但不会生成假数据。

## 运营与法律

- 所有促销及价格均以商家页面为准；只使用获得许可的商品图文素材。
- 不能擅自批量抓取受限网站或绕过反爬限制；本项目默认不抓 Amazon。
- 发布联盟链接前，核对各平台网站及二次传播的具体许可规则。
- 必须显示广告/PR 标识；处理访问统计或个人信息时完善隐私政策。
- GitHub Pages / GitHub Actions 的免费额度、商业用途限制和可用性可能变化，部署前核实最新条款。对纯商业网站建议评估 Cloudflare Pages 等替代托管。
- 定时任务是尽力执行，不保证精确触发；持续运营需要监控失败和过期商品。

## 当前限制

- 尚无真实商品入库，尚未取得任何外部 API/联盟账户凭据。
- 自动采集只生成楽天候选商品，人工核验后才发布。
- 没有真实历史价、订单归因、广告、SEO 文章或管理员认证界面。
- 站点是可部署 MVP，不是已经运营中的公网服务。
