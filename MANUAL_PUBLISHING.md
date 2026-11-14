# 在日好价：手动发布优惠

1. 在 GitHub 打开 `data/manual_deals.json`，点击编辑。
2. 从 `data/manual_example.json` 复制一条示例到数组中，填写真实信息。多个条目用英文逗号分隔。保持 JSON 格式有效。
3. `deal_type` 填 `商品降价`、`优惠券与积分` 或 `线下促销`。`price_jpy` 为日元数字；无固定价格的活动可填 `null`，同时填写 `offer_text`。
4. `url` 必须是活动或商品的官方 HTTPS 页面；`verified_at` 是你实际核对信息的时间（含日本时区 `+09:00`），`expires_at` 为截止时间，未知可不填。`location` 填线上或适用门店/地区。
5. 确认优惠仍有效后，把 `verified` 从 `false` 改成 `true`。**不要把示例商品当真实优惠发布。**
6. 点击 Commit changes。GitHub Actions 生成 `site/deals.json` 并提交到仓库，Cloudflare Pages 从 main 分支重新部署。若未自动出现，检查 GitHub Actions 运行结果，并确认 Cloudflare 监控 `main` 分支。

## 注意
- 不要凭猜测填写原价、折扣幅度、活动时间。无可核验价格时不要填 `reference_price_jpy`。
- 线下活动请明确参与门店、地区、会员限制；线上优惠注明优惠券领取条件。
- `affiliate_url` 仅填已获授权的推广链接。没有则留空，页面使用 `url`。
- GitHub 仓库是公开的，不要提交 API 密钥、个人联系方式或敏感信息。
- 页面仅显示通过检查、未过期的条目；网站不会自动验证活动真实性，发布者仍需人工核验。
