# 角色包训练数据覆盖审计（2026-06-02，2026-06-08 运行态订正）

## 结论

当前活动目录有 4 个角色包，共 136 个角色。统计字段位于 `manifest.characters[].training_stats`，`samples/` 只是预览图，不能作为训练图数量来源。

> 2026-06-08 订正：下方“本轮重建来源”保留 2026-06-02 到 2026-06-05 的历史证据链；本节表格、当前缺口清单和验证记录按当前活动包订正。后续追踪以 `docs/tracking/character-pack-batch-fill-2026-06-06.md` 为准。

| pack | 角色数 | training_stats | 当前训练图 | 少于 5 张 | 分桶缺口 | 结论 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `project_sekai` | 26 | 26/26 | 422 | 0 | 0 | 已重建，全部 `full_body/normal_proportion/chibi/expression` 四桶齐。 |
| `bangdream` | 60 | 60/60 | 360 | 0 | 10 | 当前活动包已写回第一批高置信 chibi 源；仍剩 10 个后期角色只缺可信 `chibi`。2026-06-07 BangDream 商品向二轮 108 specs / 69 accepted_for_review 全部筛拒，未写回。 |
| `zh_virtual_singers` | 16 | 16/16 | 151 | 0 | 3 | 当前活动包已完成永夜 Minus `full_body` 分桶修正，补入赤羽/牧心 normal_proportion，用确定性裁剪补入苍穹/诗岸 chibi，用 VSinger archived sticker 小批清除洛天依/言和/乐正绫/乐正龙牙/徵羽摩柯/墨清弦缺口，用 VOICEMITH/E-CAPSULE LINE 小批清除夏语遥缺口，用 Bilibili 表情小批清除星尘/海伊缺口，用 LINE creator 小批清除心华缺口，并用五维介质 Bilibili 表情小批清除赤羽/牧心/永夜 Minus chibi/expression 与苍穹/诗岸 expression 缺口；剩余 3 个中V缺口角色。 |
| `ja_virtual_singers` | 34 | 34/34 | 299 | 0 | 4 | 当前活动包已完成 KAFU/SEKAI/COKO/HARU/MAYU 官方 portrait/profile expression 分桶修正，并补入 KAFU/SEKAI/RIME/COKO FINDME 官方/授权 chibi 源。日V仍剩 Lily expression、猫村 chibi/expression，以及 HARU/MAYU chibi 缺口。 |

本轮没有把缺口伪装成完成：找不到可信 Q 版/表情/单人官方补源的角色只列为源缺口，不用盲搜首图、合并页双人图或低可信素材凑数。

## 审计口径

门槛：

- `image_count < 5`：必须列出；能从可信源补齐的重训录入。
- `全身 / 正比 / Q版 / 表情` 任一桶不足：必须列出缺失桶；能找到可信源再补。
- `samples/` 只是最多 3 张缩略预览，不能等同训练图数量。
- 权威统计为每角色 `training_stats`，包括 `image_count/forms/sources/missing_forms`。

字段映射：

| 中文桶 | manifest key |
| --- | --- |
| 全身 | `full_body` |
| 正比 | `normal_proportion` |
| Q版 | `chibi` |
| 表情 | `expression` |

## 本轮重建来源

### sidecar 构建器

`ccip-sidecar/server.py` 的 `/build-series-pack` 已支持透传 `training_stats`，并补默认 `image_count/embedded_count/sample_count`。定向测试覆盖统计字段进入 manifest 与构建响应。

### Project SEKAI

新增 `tools/enroll_pjsk_pack.py`，覆盖当前运行包的 26 人，包括 `fengxiaomeng` / `xiaoshanruixi`。

可信来源：

- Sekaipedia `Category:Cutouts of <Full Name>`：正比/全身卡面 cutout。
- `<Given>-chibi-circle.png`：Q 版。
- `MySEKAI <given> front/left/right/back.png`：MySEKAI 3D。
- `List of <Full Name> stamps`：表情 stamp。只选“只出现在该角色 stamp 页”的图片，排除多人/共用 stamp。

最终活动包：`project_sekai.charpack`，26 人，422 张，0 缺口。备份：`project_sekai.charpack.bak-20260603-053204`。

### BangDream

`tools/enroll_bangdream_pack.py --stamps 2` 已接入官方 BanG Dream! Our Notes：

- `https://bang-dream-on.bushimo.jp/.../common/index/img_<given-family>.webp`
- `https://bang-dream-on.bushimo.jp/.../common/character/img_<given-family>_2.webp`

新增 repo-local 下载缓存和 retry，避免 dry-run 成功而实际构建随机掉图。当前 `bangdream.charpack` 为 60 人、400 张，所有角色 `image_count >= 5`；Ave Mujica 5 人已补入官方迷你动画 `img_chara-*.webp` Q 版图，剩余 15 人只缺 `chibi`。新增备份：

- `bangdream.charpack.bak-20260603-094555`
- `bangdream.charpack.bak-20260603-095615-pre395`
- `bangdream.charpack.bak-20260603-211959-pre400-active`

复查结论：Bestdori `characters/all.5.json` 只到 MyGO 40 人，41-45 无角色记录，`cards/all.5.json` 对 41-45 也无卡牌，因此不能从 Bestdori LiveSD 补 41-45；但官方迷你动画「元祖！バンドリちゃん」Ave Mujica 角色页公开 `img_chara-uika/mutsumi/umiri/nyamu/sakiko.webp`，均为单角色 Q 版图，已作为 `mini_anime_chibi_*` 接入。BanG Dream! Our Notes 角色页 HTML 枚举仍只公开 `index/img_*`、`character/img_*_2`、`ogp_*` 和 `nav_*`，未发现 Yumemita/Ma'cherie/イッカダムロック 的 `chibi/sd/mini` 资源族。因此后 15 人 `chibi` 缺口保留，不使用非官方截图或盲搜图。脚本同时过滤 Bestdori 当前明确无 `trim_normal.png` 的 `res900*`、`bili_*`、`res*900/901/500/501/s01` 资源族，避免每次重训反复空撞慢源。

### 中V

`tools/enroll_virtual_singers_pack.py --pack zh` 使用精确萌娘百科页标题、图库 imageinfo 直链、redirect 备用、下载缓存。本轮新增精确文件 allowlist：

- `yongye_minus`：`Minus人設.png`、`Minus公式服.png`、永夜 Minus 声库封面等。
- `dongfang_zhizi`：东方栀子单人立绘/形象图文件。
- `hai_yi`、`cang_qiong`、`chi_yu`、`shi_an`、`mu_xin`：SynthV Wiki Official Art 的精确静态图片 URL，分别补入官方全身图、AI 图、设定/头像图；dry-run 先验证 URL 可下载后才真实重建。
- `zhiyu_moke`：VSinger 官方 `https://vsinger.com/api/vsinger` 返回的 `cover[0]`，精确 URL `https://res.vsinger.com/images/ec7b130d2e36888de23c91191223c64d.png`，作为 `official_full_vsinger_api_zhiyu_moke_cover_0` 接入；同 API 的 `cover[1]` / `cover[2]` 在运行时会误判为 `kaito`，已拒绝接入。
- `luo_tianyi`：VSinger 官方 `https://vsinger.com/api/vsinger` 的 `models` 条目公开“洛天依Q版公式服”，其 `cover_img` 为精确 URL `https://res.vsinger.com/images/cffa18b20ce29010364f33ae94c6a8b0.jpg`，目检为干净单角色 Q版洛天依，作为 `official_chibi_vsinger_api_luo_tianyi_q_model_cover` 接入；对应 7MB 官方 zip 只含 MMD 材质贴图，未直接作为训练图来源。
- `cang_qiong` / `shi_an`：Medium5 / SynthV Wiki concept sheet 只接入确定性单角色 front crop，分别为 `synthvwiki_chibi_crop_cangqiong_concept_front` 与 `synthvwiki_chibi_crop_shian_concept_front`，计入 `chibi`；整张 sheet 分别含苍穹+赤羽、诗岸+ZERO，且不是 normal_proportion，拒绝直录。
- VSinger archived sticker 小批：`luo_tianyi` 接入 1 张 expression；`yan_he`、`yuezheng_ling`、`yuezheng_longya`、`zhiyu_moke`、`mo_qingxian` 各接入 1 张 expression + 1 张 chibi，来源均为 Luminous/img.lty.fun 归档表情图。该页面声称提取自 VSINGER 官方 QQ/微信表情且版权归上海禾念，但不是 VSinger 一手官网 endpoint，因此 source 命名为 `archived_*` 而不是 `official_*`。
- VOICEMITH/E-CAPSULE LINE STORE 小批：LINE product `5077007` 页面标题 `夏語遙(3)`、作者 `飛天膠囊數位科技有限公司`、版权 `©VOICEMITH.All Rights Reserved`，强绑定夏语遥；40 张贴图中只接入 `/identify` 与全包 collision 稳定的 `98039968` / `98039985`，分别补 `xia_yuyao` 的 `chibi` / `expression` 桶。其它同产品贴图因 top1 误撞、超阈值或 margin 过窄继续拒绝，不能批量清表。
- Bilibili 表情小批：B 站表情包 `264`（星尘，item_id `6077`）与 `441`（海伊，item_id `36777`）来源绑定后，只接入 `/identify` 与全 136 top8 collision 稳定的 4 张单图：星尘 `4391` / `4401`，海伊 `7648` / `7659`，分别补二者的 `chibi` / `expression` 桶。星尘 `4393` / `4396` 当前误撞其它角色，海伊 `7656` / `7657` 不稳或未命中，星尘新年包 `755` 仅作弱候选，均不接入。
- 心华 LINE creator 小批：LINE product `1245282` 页面标题 `台灣V家虛擬歌姬-心華`、描述强绑定心华出道一周年纪念贴图，作者 `FACIO YUMEI`；因作者不能核成官方权利方 endpoint，source 命名为 `line_creator_*` 而不是 `official_*`。只接入 `/identify` 与全 136 top8 collision 稳定的 `9950512` / `9950513`，分别补 `xin_hua` 的 `chibi` / `expression` 桶。LINE 搜索误命中 product `8632068` 实为 `暖男 冠華`，product main 图和 `1245282` 中其它贴图继续拒绝。
- 五维介质 Bilibili 表情小批：Bilibili 活动 `五维介质-启程之音`（`act_id=107174`、`lottery_id=107180`）强绑定平行四界/五维介质角色，反查 package `7995`（动态表情包，item_id `1745465728001`）与 `7996`（静态表情包，item_id `1745238293001`），并参考旧五维包 `3863` / `3911`。90 张静态 PNG 中只接入 `/identify` 与全 136 top8 collision 稳定的 8 张单图：苍穹 `7996_109372` expression；赤羽 `7995_109347` chibi、`7995_109362` expression；诗岸 `7996_109374` expression；牧心 `3863_53979` chibi、`7996_109384` expression；永夜 Minus `3863_53963` chibi、`7996_109370` expression。东方栀子无 top1 命中，`7996_109383` 超阈值且不稳，非白名单表情继续拒绝；苍穹/诗岸表情图不用于清 `normal_proportion`。

当前 `zh_virtual_singers.charpack` 为 16 人、151 张，已无少于 5 张角色；洛天依、言和、乐正绫、乐正龙牙、徵羽摩柯、墨清弦、夏语遥、星尘、海伊、心华、赤羽、牧心、永夜 Minus 已四桶齐；苍穹/诗岸已补 `chibi/expression`、仍缺 `normal_proportion`；东方栀子仍缺 `chibi/expression`。新增备份：

- `zh_virtual_singers.charpack.bak-20260603-104652-pre97`
- `zh_virtual_singers.charpack.bak-20260603-110623-pre116-active`
- `zh_virtual_singers.charpack.bak-20260603-215124-pre117-active`
- `zh_virtual_singers.charpack.bak-20260604-165510-pre118-active`
- `zh_virtual_singers.charpack.bak-20260607-194441-pre-medium5-chibi-crop-active`
- `zh_virtual_singers.charpack.bak-20260607-203504-pre-vsinger-stickers-active`
- `zh_virtual_singers.charpack.bak-20260607-211439-pre-xia-yuyao-line-sticker-active`
- `zh_virtual_singers.charpack.bak-20260607-215202-pre-bilibili-emote-active`
- `zh_virtual_singers.charpack.bak-20260607-223821-pre-xin-hua-line-sticker-active`
- `zh_virtual_singers.charpack.bak-20260608-011343-pre-qicheng-bilibili-emote-active`

### 日V

`tools/enroll_virtual_singers_pack.py --pack ja` 新增精确日V Moegirl 标题、日V exact-title gallery、官方补源白名单：

- AHS/SSW：音街鳗、结月缘、绁星灯、歌爱雪、东北俊子、东北切蒲英、京町精华、追傩酱等。
- TOKYO6：小春六花、夏色花梨、花隈千冬官方视觉和设定图。
- KAMITSUBAKI 音乐的同位体：可不、星界、里命、狐子、羽累独立产品页。
- Piapro/Crypton：本家初音、巡音流歌、MEIKO、KAITO、镜音铃/连单人图；未使用镜音双人产品图凑数。
- Sonicwire/Crypton 官方图片包 zip：`rinlenv4x_images.zip`、`rinlennt_images.zip`、`mikunt_images.zip`、`lukav4x_images.zip`、`meikov3_images.zip`、`kaitov3_images.zip`。脚本新增 zip member 下载与缓存，只接入单人 `sd`/`settei`/全身成员，不接入合图。
- Internet/VOCALOID/v-flower 官方页：GUMI、神威乐步、Lily、MAYU、v flower、猫村伊吕波等。
- A.I.VOICE 琴叶茜/葵单人表情图，新增 `official_expression_* -> expression` 分桶。
- A.I.VOICE 官方素材：`aivoice2_akane1.zip` / `aivoice2_aoi1.zip` 的单人 normal 立绘归 `normal_proportion`，`aivoice2_akane_sd.zip` / `aivoice2_aoi_sd.zip` 的 SD 图归 `chibi`；脚本使用 zip 内图片序号选择器，避免日文文件名本地解码差异。
- AHS 官方 SD 素材 zip：`ahs_sdcharactor.zip` 精确接入 `kaaiyuki.png`、`tsurumakimaki.png`，补 `歌爱雪` / `弦巻マキ` 的 `chibi` 桶。
- TOKYO6 官方差分 zip：`koharu_rikka_sabun_files.zip`、`natsuki_karin_sabun_files.zip`、`hanakuma_chifuyu_sabun_files.zip` 精确接入单人差分 PNG，补 `小春六花` / `夏色花梨` / `花隈千冬` 的 `expression` 桶。
- LINE STORE 官方贴图包 `TOKYO6 Characters Sticker`（作者 `TOKYO6 ENTERTAINMENT`）：通过 `productInfo.meta` 枚举到单张 sticker ID，只接入人工核对为单角色的 `648064406/648064412/648064408`，分别补 `koharu_rikka` / `natsuki_karin` / `hanakuma_chifuyu` 的 `chibi` 桶，使三人四桶齐。
- IA 官方站单人视觉图：接入 `mob_main_202010_-1.jpg` 作为 `ia` 的 `full_body`，并接入官方 `IA/05` 封面 `IA05_jk-scaled.jpg` 作为 `profile_art` 稳定 centroid；不把封面图计入 `full_body/normal_proportion/chibi/expression` 任一桶。离线模拟后真实重建，`mob_main_202010` 的 `/identify` diff 从未过阈值的约 `0.17960` 降至 `0.17699`，确认不是只补表格。
- LINE STORE 官方贴图包 `Hatsune Miku: All Together`（作者 `CRYPTON FUTURE MEDIA, INC`）：只接入人工核对为单角色的 6 张贴图，补本家 `vocaloid_hatsune_miku` / `vocaloid_kagamine_rin` / `vocaloid_kagamine_len` / `vocaloid_megurine_luka` / `vocaloid_meiko` / `vocaloid_kaito` 的 `expression` 桶；跳过多人贴图和难以区分角色的贴图。
- LINE STORE 官方贴图包 `Tsurumaki Maki`（作者 `AHS Co. Ltd.`）：只接入人工核对为单角色的 `375929074`，补 `tsurumaki_maki` 的 `expression` 桶。
- LINE STORE 官方贴图包 `Kaai Yuki`（作者 `AHS Co. Ltd.`）：只接入人工核对为单角色的 `5105717`，补 `kaai_yuki` 的 `expression` 桶；该角色仍缺 `normal_proportion`，不标完成。
- LINE STORE 官方贴图包 `Yuzuki Yukari` / `Kizuna Akari`（作者 `VOCALOMAKETS`）：分别先接入人工核对为单角色的 `796527` / `153063566`，补 `yuzuki_yukari` / `kizuna_akari` 的 `expression` 桶；后续追加 Q版见下一条，正比桶见官方设定资料条目。
- LINE STORE 官方贴图包 `Gumi Animated Stickers`（作者 `INTERNET Co., Ltd.`）与 `Otomachi Una`（作者 `MTK`）：分别只接入人工核对为单角色的 `17915992/17915994` 与 `15392046/15392026`，补 `gumi` / `otomachi_una` 的 `chibi` / `expression` 桶；正比桶见官方立绘条目。
- LINE STORE 官方贴图包 `Yuzuki Yukari` / `Kizuna Akari`（作者 `VOCALOMAKETS`）追加人工核对为单角色的 `796519` / `153063560`，补 `yuzuki_yukari` / `kizuna_akari` 的 `chibi` 桶；正比桶见官方设定资料条目。
- LINE STORE 官方贴图包 `CAMUI GACKPO STICKER`（作者 `INTERNET Co., Ltd.`）通过 `productInfo.meta` 枚举到单张 sticker ID，只接入人工核对为单角色的 `339791279/339791316`，补 `kamui_gakupo` 的 `chibi` / `expression` 桶；正比桶见 SSW 官方插画页条目。
- LINE STORE 官方贴图包 `TETO STAMP 1`（作者 `Oyamano`，页面内含 `twindrill` 元信息）：只接入人工核对为单人重音テト的 `237904694` / `237904695`，分别补 `kasane_teto` 的 `chibi` / `expression` 桶，使该角色四桶齐。
- LINE STORE 官方贴图包 `IA Official Sticker Vol.1` / `ONE Official Sticker Vol.1`（作者 `1st PLACE`）：分别只接入人工核对为单角色的 `14927590/14927591` 与 `25943272/25943273`，补 `ia` / `one` 的 `chibi` / `expression` 桶；`ia` 仍缺 `full_body`，`one` 仍缺 `normal_proportion`，不标完成。
- LINE STORE 官方贴图包 `Sticker Hana-chan`（作者 `Gyroid Co., Ltd.`，描述明确为 v flower）：只接入人工核对为单人 v flower 的 `11849125` / `11849127`，分别补 `v_flower` 的 `chibi` / `expression` 桶，使该角色四桶齐。
- LINE STORE 官方贴图包 `Tohoku Zunko` / `Tohoku Kiritan`（页面含 `SSS LLC.` 元信息）：分别只接入人工核对为单角色的 `95596/95597` 与 `4912144/4912145`，补 `tohoku_zunko` / `tohoku_kiritan` 的 `chibi` / `expression` 桶；两者仍缺 `normal_proportion`，不标完成。
- LINE STORE 官方贴图包 `Seika Kyomachi 70th Anniversary Stickers`（作者 `Seika town`）与 `Onikko Hunter Tsuina-chan vol.01`（作者 `Onikko Hunter Tsuina-chan Project`）：分别只接入人工核对为单角色的 `787267833/787267834` 与 `398851390/398851391`，补 `kyomachi_seika` / `tsuina_chan` 的 `chibi` / `expression` 桶；`tsuina_chan` 仍缺 `normal_proportion`，不标完成。
- SSW / 音街ウナ官网 / VOCALOMAKETS / 京町セイカ官网官方正比来源：接入 `official_sheet_gumi_aivoice2_standing`、`official_sheet_gackpoid_v3_native`、`official_sheet_lily_v3_front`、`official_sheet_una_aivoice2_standing`、`official_sheet_vocalomakets_yukari_aivoice2_config`、`official_sheet_vocalomakets_akari_aivoice2_config`、`official_sheet_seika_town_character_hp1`。其中 GUMI、神威乐步、音街鳗、结月缘、绁星灯、京町精华由此四桶齐；Lily 先修复 `normal_proportion`，后续 Animove 官方 SD 图修复 `chibi`。
- Animove / Lily 官方 goods 页：接入 `official_chibi_animove_lily_goods_sd_20130401`（`https://animove.jp/lily/goods/2013/04/01/130401_sd.jpg`），目检为单角色 SD/Q版 Lily，重建后 `/identify` 命中 `lily 0.070962653`，补 Lily 的 `chibi` 桶；Lily 当前仍缺 `expression`，不标完成。
- AHS press 官方单人插画：接入 `official_sheet_ahs_press_yuki_vocaloid4_illust`、`official_sheet_ahs_press_iroha_synthv2_illust`、`official_sheet_ahs_press_zunko_voicepeak_illust`、`official_sheet_ahs_press_kiritan_voicepeak_illust`、`official_sheet_ahs_press_tsuina_synthv2_illust`。其中歌爱雪、东北俊子、东北切蒲英、追傩酱由此四桶齐；猫村伊吕波只修复 `normal_proportion`，仍缺 `chibi/expression`。
- KAMITSUBAKI 音楽的同位体官方 GIF：官方页面给出 MediaFire 配布文件夹，脚本通过精确 quickkey URL 下载 GIF，并固定抽取中段角色帧转 PNG，避免 GIF 首帧空白/文字污染 embedding。接入 `official_expression_kamitsubaki_gif4_kafu_frame20`、`official_expression_kamitsubaki_gif2_sekai_frame28`、`official_expression_kamitsubaki_gif4_rime_frame40`、`official_expression_kamitsubaki_gif3_coko_frame36`，分别补 `kafu` / `sekai` / `rime` / `coko` 的 `expression` 桶。曾测试的 `official_expression_kamitsubaki_gif2_coko_frame36` 会被 `/identify` 误判为 `yan_he`，已拒绝接入。
- FINDME STORE / KAMITSUBAKI 音楽的同位体 官方/授权商品图：只接入通过 visual review、`/identify` 与全 136 top8 collision 的 4 张单角色 chibi 图：`official_chibi_findme_kafu_1st_anniv_plush_mascot`、`official_chibi_findme_sekai_1st_anniv_plush_mascot`、`official_chibi_findme_rime_niconico2023_plush_front`、`official_chibi_findme_coko_niconico2023_plush_front`。HARU front 当前 top1 为 KAFU，背面图不稳定，包装/专辑/展示图不是干净 chibi 源，均拒绝接入。
- Piapro 官方“投稿可能なキャラクター”页：接入 `official_sheet_piapro_sekai_standing`、`official_sheet_piapro_haru_standing`、`official_sheet_piapro_one_standing`、`official_sheet_piapro_mayu_standing`，分别补 `sekai` / `haru` / `one` / `mayu` 的 `normal_proportion` 桶；`one` 因此四桶齐。`kafu` / `rime` 的 Piapro 官方站姿图虽为一手图，但运行态分别误撞 `izumi_houka` / `yakura_yomogi`，离线 centroid 模拟后也不能把 top1 拉回本人，已拒绝接入。

当前 `ja_virtual_singers.charpack` 为 34 人、299 张，已无少于 5 张角色；`kotonoha_akane` / `kotonoha_aoi`、本家 Crypton 6 人、`tsurumaki_maki`、`kasane_teto`、`v_flower`、`koharu_rikka`、`natsuki_karin`、`hanakuma_chifuyu`、`ia`、`one`、`gumi`、`kamui_gakupo`、`otomachi_una`、`yuzuki_yukari`、`kizuna_akari`、`kyomachi_seika`、`kaai_yuki`、`tohoku_zunko`、`tohoku_kiritan`、`tsuina_chan`、`kafu`、`sekai`、`rime`、`coko` 已四桶齐。`lily` 仍缺 `expression`；`nekomura_iroha` 仍缺 `chibi/expression`；`haru` / `mayu` 仍缺 `chibi`。新增备份：

- `ja_virtual_singers.charpack.bak-20260603-101259-pre104`
- `ja_virtual_singers.charpack.bak-20260603-101841-pre131`
- `ja_virtual_singers.charpack.bak-20260603-102414-pre163`
- `ja_virtual_singers.charpack.bak-20260603-103159-pre179`
- `ja_virtual_singers.charpack.bak-20260603-104003-pre200`
- `ja_virtual_singers.charpack.bak-20260603-111855-pre206-active`
- `ja_virtual_singers.charpack.bak-20260603-112837-pre214-active`
- `ja_virtual_singers.charpack.bak-20260603-115604-pre218-active`
- `ja_virtual_singers.charpack.bak-20260603-133759-pre218-active`
- `ja_virtual_singers.charpack.bak-20260603-135701-pre235-active`
- `ja_virtual_singers.charpack.bak-20260603-141546-pre241-active`
- `ja_virtual_singers.charpack.bak-20260603-142903-pre242-active`
- `ja_virtual_singers.charpack.bak-20260603-144952-pre243-active`
- `ja_virtual_singers.charpack.bak-20260603-151227-pre245-active`
- `ja_virtual_singers.charpack.bak-20260603-154028-pre247-active`
- `ja_virtual_singers.charpack.bak-20260603-155618-pre249-active`
- `ja_virtual_singers.charpack.bak-20260603-161516-pre255-active`
- `ja_virtual_singers.charpack.bak-20260603-164304-pre259-active`
- `ja_virtual_singers.charpack.bak-20260603-171441-pre263-active`
- `ja_virtual_singers.charpack.bak-20260603-174005-pre269-active`
- `ja_virtual_singers.charpack.bak-20260603-175758-pre272-active`
- `ja_virtual_singers.charpack.bak-20260603-181500-pre273-active`
- `ja_virtual_singers.charpack.bak-20260603-190348-pre274-active`
- `ja_virtual_singers.charpack.bak-20260603-195400-pre280-active`
- `ja_virtual_singers.charpack.bak-20260603-200414-replaced-active`
- `ja_virtual_singers.charpack.bak-20260603-203304-pre285-active`
- `ja_virtual_singers.charpack.bak-20260603-203822-replaced-active`
- `ja_virtual_singers.charpack.bak-20260603-204251-pre289-bad-coko-frame-active`
- `ja_virtual_singers.charpack.bak-20260603-204758-replaced-bad-coko-frame-active`
- `ja_virtual_singers.charpack.bak-20260604-151006-pre291-active`
- `ja_virtual_singers.charpack.bak-20260604-152925-pre293-active`
- `ja_virtual_singers.charpack.bak-20260605-030431-pre293-active`
- `ja_virtual_singers.charpack.bak-20260607-181934-pre-kamitsubaki-chibi-active`

已复查但不接入：

- AHS `maki_sozai.zip`：体积小但内容为标志、T-shirt、徽章素材，不含弦巻マキ角色表情图。
- AHS `maki_illust.zip` / `maki_illust2.zip`：分别约 86MB / 482MB，默认重训下载成本过高；未证明其中有小体积、单人、可稳定抽取的表情成员前，不作为复跑来源。
- AHS 歌愛ユキ おまけ页 `sd_yuki_*.bmp`、`yuki01/02.png`：均为官方单人图，但不是 clean standing/设定图；本轮使用 AHS press `vocaloid4_yuki_illust.jpg` 修复 `normal_proportion`。
- AHS 歌愛ユキ产品页 `img.jpg` 等图已目检，虽然是单人图，但带 VOCALOID 产品标注或商品信息；暂不把它们从 `full_body` 重分类为 `normal_proportion`，避免只为清缺口改标签。
- AHS 追傩酱 `tsuina_illust.zip` 为官方包且支持 HTTP Range，但 `Content-Length=566027607`，当前脚本只支持整包下载；未实现稳定远程 zip 成员抽取前，不接入默认重训路径。本轮使用 AHS press 小体积直链插画修复 `normal_proportion`。
- KAMITSUBAKI STUDIO LINE 贴图包 `KAF #1` / `isekaijoucho #1` / `RIM #1` / `KOKO #01` / `HARUSARUHI #2/#3`：作者可信，但角色是花譜/异世界情绪/理芽/幸祜/春猿火等原虚拟歌手，不是 `可不/星界/里命/狐子/羽累` 音楽的同位体本体；暂不把这些贴图接入同位体角色包，避免 centroid 混入非精确角色。
- KAMITSUBAKI 官方 MediaFire 配布文件夹：owner 为 `KAMITSUBAKI RECORD`，当前只见 first-fourth batch，含 KAFU/SEKAI/RIME/COKO GIF，未发现 HARU；这些 GIF 是动作/表情帧，不是 Q版，因此只作为 `expression` 候选，不用于清 `chibi`。
- KAMITSUBAKI comics 页：存在官方图，但多为多格、多角色、带对白气泡页面；手工 panel crop 噪声高，未实现稳定裁剪和强验证前不接入训练。
- Piapro 官方角色页 `kafu` / `rim` 站姿图：官方一手且目检为单人正比，但当前运行态分别命中 `izumi_houka 0.152659729` / `yakura_yomogi 0.161006689`；离线模拟把它们加入目标 centroid 后，KAFU 仍排到第 6、RIME 仍未进入前 6，因此拒绝接入。Piapro `lily` / `iroha` 站姿图也为一手图且能命中本人，但 Lily 当前只缺 `expression`、猫村仍缺 `chibi/expression`，正比站姿图不能清桶，暂不接入。
- AHS `ahs_sdcharactor.zip` 复核：包含已接入的歌愛ユキ、弦巻マキ等 SD 图，但没有 Lily、MAYU、猫村伊吕波，不可用于清这三人的 `chibi` 缺口。
- MAYU 官方站 `mayusan.jp`：profile / wallpaper / standing 等均为正常比例或壁纸类官方图，不是当前缺失的 `chibi` / `expression`，暂不接入。
- BanG Dream! TV 动画「ゆめ∞みた」角色页 `img_character-list-arale/nonoka/ritsu/miyako/yuno.webp`：官方单人图，但目检为正比半身图，不是 Q 版；Yumemita 当前缺口仅为 `chibi`，因此不接入清桶。
- BanG Dream! `Ma'cherie` / `イッカダムロック` 相关公开搜索与官方站资源未发现可直链、单人、Q 版的 `chibi/sd/mini` 家族；继续保留 10 人 `chibi` 缺口。
- LINE STORE 官方作者页复核：`INTERNET Co., Ltd.` 当前仅列出 GUMI 贴图，未找到 Lily；`AHS Co. Ltd.` 当前列出歌爱雪、弦巻マキ、フリモメン等 9 个产品，未找到猫村伊吕波；`1st PLACE` 有 IA/ONE 贴图但只能补 Q版/表情，不能补 ONE 的 `normal_proportion`。
- KAMITSUBAKI 音楽的同位体 product 页复核：`kafu/sekai/rime/coko/haru` 页面只公开 KV、about、package、howto 等图，未发现设定/立ち絵下载或 SD/Q版资源；不把商品图或原虚拟歌手贴图挪作同位体 `normal_proportion/chibi`。
- FINDME/KAMITSUBAKI plush 复核：KAFU/SEKAI 1周年 mascot 与 RIME/COKO niconico2023 front 已接入；HARU niconico2023 front 当前 top1 为 `kafu`，back 图与其它背面图碰撞不稳，包装/专辑/展示图不是干净 chibi 源，均不用于清缺口。
- VSinger/洛天依相关表情源复核：`vsinger.com` 官方 SPA 未公开表情包下载。`luotianyi.vc` / `img.lty.fun` 属于非一手镜像；本轮只在人工审核、`/identify` 与全包 collision 通过后，以 `archived_*` 名义接入 11 张 VSinger 归档表情源，不把它们标为 `official_*`。VSinger 官方“洛天依Q版公式服” zip 可下载但内部为 MMD 材质贴图，不作为训练图；只接入同 API 暴露的干净单角色 `cover_img`。徵羽摩柯七周年表情候选多张 top1 误撞 `kaito`，已拒绝。
- 夏语遥 LINE product `5077007` 复核：页面元数据强绑定 VOICEMITH/E-CAPSULE 夏语遥，但不批量接入。只有 `98039968` / `98039985` 两张通过当前运行态 `/identify` 与全 136 top8 collision；其它贴图作为误撞/不稳负例保留。
- 心华 LINE product `1245282` 复核：页面标题和描述强绑定心华，但作者不是可核成官方权利方的一手 endpoint；只以 `line_creator_*` 接入 `9950512` / `9950513`。LINE product `8632068` 是 `暖男 冠華` by `CuckCuck`，不是心华，继续作为同名误命中负例。
- 苍穹/诗岸 Medium5 concept sheet 复核：整图分别含另一名角色（赤羽 / ZERO），不能作为 clean single-character source；本轮只用确定性目标角色裁剪清 `chibi`，不清 `normal_proportion`。
- 五维介质 Bilibili 表情包复核：启程之音活动和 package `7995/7996` 来源强绑定五维介质，但只接入 8 张白名单稳定图；东方栀子无 top1 命中，`7996_109383` 超阈值且不稳，非白名单表情继续作为负例。苍穹/诗岸已由表情图清 `expression`，但仍不能把表情图当作 `normal_proportion`。

## 当前缺口清单

### Project SEKAI

无少于 5 张或四桶不足角色。

### BangDream

无少于 5 张角色。当前剩余 10 个角色只缺 `chibi`：

`shiomi_hotaru`, `izawa_natsume`, `kotohira_nagi`, `hamasaki_mahoro`, `izumi_houka`, `suga_raika`, `mahashi_miku`, `yakura_yomogi`, `umezato_chieri`, `shinomiya_shizuku`。

### 中V

无少于 5 张角色。仍有分桶缺口：

| 缺失桶 | 角色 |
| --- | --- |
| `chibi,expression` | `dongfang_zhizi` |
| `normal_proportion` | `cang_qiong`, `shi_an` |

### 日V

无少于 5 张角色。仍有分桶缺口：

| 缺失桶 | 角色 |
| --- | --- |
| `expression` | `lily` |
| `chibi,expression` | `nekomura_iroha` |
| `chibi` | `haru`, `mayu` |

## 验证记录

结构验证：

- `bangdream.charpack`: `chars=60 stats=60/60 total_images=360 npz_keys=60 dims=[(768,)] sample_dirs=60 under5=0 gaps=10`
- `project_sekai.charpack`: `chars=26 stats=26/26 total_images=422 npz_keys=26 dims=[(768,)] sample_dirs=26 under5=0 gaps=0`
- `zh_virtual_singers.charpack`: `chars=16 stats=16/16 total_images=151 npz_keys=16 dims=[(768,)] sample_dirs=16 under5=0 gaps=3`
- `ja_virtual_singers.charpack`: `chars=34 stats=34/34 total_images=299 npz_keys=34 dims=[(768,)] sample_dirs=34 under5=0 gaps=4`
- 全包 `duplicate_ids={}`。

运行态验证：

- 只执行 `docker compose restart ccip-sidecar`，NapCat 未重启/未 recreate。
- `curl http://localhost:8620/health`：`status=ok`, `pack_count=4`, `character_count=136`, `registry_version=7a60b4f27c86`, `api_version=2026-06-01.v1`。
- `docker inspect napcat --format '{{.Created}} {{.State.Status}}'`：`2026-05-28T10:56:06.736616338Z running`。
- 2026-06-07 日V expression rebucket 复核：`official_profile_kafu_about`、`official_profile_sekai_about`、`official_profile_coko_about1`、`official_profile_haru_about`、`official_profile_mayu_loves` 的 `/identify` 均命中本人，容器内全 136 top8 collision 第一名均为目标。
- 2026-06-07 日V FINDME chibi 复核：新增 KAFU `0.060864493`、SEKAI `0.081995755`、RIME `0.107475802`、COKO `0.133097947` 四张 chibi 源 `/identify` 均命中本人，容器内全 136 top8 collision 第一名均为目标；top2 分别为 `kizuna_akari 0.128774717`、`yan_he 0.220393762`、`yan_he 0.167120725`、`yan_he 0.196991414`。
- 2026-06-07 中V Medium5 chibi crop 复核：新增苍穹 crop `/identify` 命中 `cang_qiong 0.0737996176`，全 136 top8 第一名 `cang_qiong`、第二名 `hinomori_shizuku 0.159621`；新增诗岸 crop `/identify` 命中 `shi_an 0.0572203174`，全 136 top8 第一名 `shi_an`、第二名 `nakamachi_arale 0.097765528`。
- 2026-06-07 中V VSinger archived sticker 复核：新增 11 张 Luminous/img.lty.fun 归档表情源 `/identify` 均命中目标，容器内全 136 top8 collision 第一名均为目标，最低 top1-top2 margin 约 `0.0713`；活动中V包从 124 图增至 135 图，VSinger 六人缺口清除。
- 2026-06-07 中V夏语遥 LINE 复核：新增 `98039968` / `98039985` 两张 VOICEMITH/E-CAPSULE LINE 贴图源 `/identify` 均命中 `xia_yuyao`，diff `0.107345752` / `0.098708108`；容器内全 136 top8 collision 第一名均为目标，margin 约 `0.1409` / `0.1553`；活动中V包从 135 张图增至 137 张图，夏语遥缺口清除。
- 2026-06-07 中V Bilibili 表情复核：新增星尘 `4391` / `4401` 两张 B 站表情源 `/identify` 均命中 `xingchen`，diff `0.069077484` / `0.049320612`；新增海伊 `7648` / `7659` 均命中 `hai_yi`，diff `0.088419318` / `0.111666501`；容器内全 136 top8 collision 第一名均为目标，最窄 margin `0.088582411`；活动中V包从 137 张图增至 141 张图，星尘/海伊缺口清除。
- 2026-06-07 中V心华 LINE creator 复核：新增 `9950512` / `9950513` 两张 LINE creator 贴图源 `/identify` 均命中 `xin_hua`，diff `0.145203948` / `0.140470535`；容器内全 136 top8 collision 第一名均为目标，margin `0.069639474` / `0.077715009`；活动中V包从 141 张图增至 143 张图，心华缺口清除。
- 2026-06-08 中V五维介质 Bilibili 表情复核：新增 8 张启程之音/旧五维包白名单源 `/identify` 均命中目标，diff `0.044722285` 到 `0.100470096`；容器内全 136 top8 collision 第一名均为目标，最窄 margin `0.041792527`；活动中V包从 143 张图增至 151 张图，赤羽/牧心/永夜 Minus 缺口清除，苍穹/诗岸只剩 `normal_proportion`，东方栀子仍缺 `chibi/expression`。
- `/identify` 抽检均命中期望 ID：`hatsune_miku`, `kagamine_rin`, `vocaloid_hatsune_miku`, `vocaloid_kagamine_rin`, `vocaloid_kagamine_len`, `vocaloid_megurine_luka`, `vocaloid_meiko`, `vocaloid_kaito`, `kotonoha_akane`, `kotonoha_aoi`, `kaai_yuki`, `tsurumaki_maki`, `gumi`, `otomachi_una`, `kamui_gakupo`, `lily`, `kasane_teto`, `ia`, `one`, `mayu`, `v_flower`, `tohoku_zunko`, `tohoku_kiritan`, `kyomachi_seika`, `tsuina_chan`, `yuzuki_yukari`, `kizuna_akari`, `koharu_rikka`, `natsuki_karin`, `hanakuma_chifuyu`, `nekomura_iroha`, `kafu`, `sekai`, `rime`, `coko`, `luo_tianyi`, `hai_yi`, `cang_qiong`, `mu_xin`, `zhiyu_moke`, `kotohira_nagi`, `togawa_sakiko`, `misumi_uika`, `wakaba_mutsumi`, `yahata_umiri`, `yutenji_nyamu`。其中本家 Crypton、琴葉茜/葵、AHS SD、TOKYO6 差分、Animove Lily 官方 SD、IA 官方视觉图、AHS/VOCALOMAKETS/Internet/MTK/TWINDRILL/1st PLACE/Gyroid/SSS/Seika Town/Tsuina Project LINE、Piapro 官方角色页、VSinger 官方 API、KAMITSUBAKI 音楽的同位体 GIF、BangDream 官方迷你动画 Ave Mujica Q 版图均使用新增官方 zip/LINE/Piapro/API/GIF/webp 图做运行时样本，验证新增样本已进入活动包；PJSK 同名角色未互串。本轮新增正比源 `/identify` 全部过阈值：`gumi 0.118652903`、`kamui_gakupo 0.030937895`、`otomachi_una 0.080982067`、`yuzuki_yukari 0.053368326`、`kizuna_akari 0.095096774`、`kyomachi_seika 0.028461274`、`kaai_yuki 0.018740641`、`nekomura_iroha 0.127991751`、`tohoku_zunko 0.081979223`、`tohoku_kiritan 0.043052513`、`tsuina_chan 0.039699901`、`sekai 0.035270505`、`haru 0.022259114`、`one 0.040175948`、`mayu 0.011986990`、`zhiyu_moke 0.027428376`，阈值均为 `0.178475114`；新增 Animove Lily 官方 SD 图 `/identify` 与 `/identify-multi` 均命中 `lily 0.070962653`；新增 VSinger 洛天依 Q版模型封面 `/identify` 命中 `luo_tianyi 0.163453937`；新增 KAMITSUBAKI GIF 固定帧也全部命中：`kafu 0.046182249`、`sekai 0.054937262`、`rime 0.102273606`、`coko 0.029642714`；新增 BangDream mini-anime Q 版也全部命中：`misumi_uika 0.082722224`、`wakaba_mutsumi 0.066523850`、`yahata_umiri 0.053393554`、`yutenji_nyamu 0.044979867`、`togawa_sakiko 0.045270219`。对应旧 LINE/SD chibi/expression 回归抽检也均继续命中；曾误撞 `yan_he` 的 `COKO` 第二弹 frame36 已拒绝接入，VSinger API `zhiyu_moke` 的 `cover[1]` / `cover[2]` 曾误撞 `kaito`，Piapro `kafu` / `rim` 站姿图曾误撞 `izumi_houka` / `yakura_yomogi`，均已拒绝接入。sidecar 容器内对新增 Ave Mujica 5 图计算 `ccip_difference` top3，第一名均为期望角色，第二名差距约 `0.083854` 到 `0.194083`；对智御墨客 `cover[0]` 计算 top8，第一名 `zhiyu_moke 0.027428376`，第二名 `kaito 0.110227354`，top1-top2 间隔约 `0.082799`；对新增洛天依 Q版封面计算全 136 角色 top10，第一名 `luo_tianyi 0.163453937`，第二名 `ia 0.214880824`，top1-top2 间隔约 `0.051427`；对新增 Lily SD 图计算全 136 角色 top10，第一名 `lily 0.070962653`，第二名 `vocaloid_kagamine_rin 0.125846729`，top1-top2 间隔约 `0.054884`；对新增 Piapro 4 图计算 top8，第一名均为目标角色，第二名分别为 `shiomi_hotaru 0.125532851`、`kafu 0.167983741`、`mahashi_miku 0.189226270`、`minato_yukina 0.183030874`。

静态/测试：

- `python -m py_compile ...` 通过。
- `uv run ruff check ...` 通过。
- `git diff --check -- ...` 通过。
- 2026-06-07 日V分桶/FINDME chibi 定向：`ruff check tools/enroll_virtual_singers_pack.py tests/test_enroll_virtual_singers_pack.py` 通过；`pyright ...` 为 0 errors；`PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_enroll_virtual_singers_pack.py -q` 为 `35 passed`。
- 2026-06-07 中V Medium5 chibi crop 定向：`ruff check tools/enroll_virtual_singers_pack.py tests/test_enroll_virtual_singers_pack.py` 通过；`pyright ...` 为 0 errors；`PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_enroll_virtual_singers_pack.py -q` 为 `38 passed`。
- 2026-06-07 中V VSinger archived sticker 定向：`ruff check tools/enroll_virtual_singers_pack.py tests/test_enroll_virtual_singers_pack.py` 通过；`pyright ...` 为 0 errors；`PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_enroll_virtual_singers_pack.py -q` 为 `40 passed`。
- 2026-06-07 中V夏语遥 LINE 定向：`ruff check tools/enroll_virtual_singers_pack.py tests/test_enroll_virtual_singers_pack.py` 通过；`pyright ...` 为 0 errors；`PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_enroll_virtual_singers_pack.py -q` 为 `42 passed`。
- 2026-06-07 中V Bilibili 表情定向：`ruff check tools/enroll_virtual_singers_pack.py tests/test_enroll_virtual_singers_pack.py` 通过；`pyright ...` 为 0 errors；`PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_enroll_virtual_singers_pack.py -q` 为 `44 passed`。
- 2026-06-07 中V心华 LINE creator 定向：`ruff check tools/enroll_virtual_singers_pack.py tests/test_enroll_virtual_singers_pack.py` 通过；`pyright ...` 为 0 errors；`PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_enroll_virtual_singers_pack.py -q` 为 `46 passed`。
- 2026-06-08 中V五维介质 Bilibili 表情定向：`ruff check tools/enroll_virtual_singers_pack.py tests/test_enroll_virtual_singers_pack.py` 通过；`pyright ...` 为 0 errors；`PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_enroll_virtual_singers_pack.py -q` 为 `48 passed`。

## 完成判断

原始目标中的“少于 5 张”已清零，但四桶分区目标尚未完全完成：

- BangDream 仍有 10 个晚近角色缺可信 Q 版。
- 中V 3 人仍有至少一个分桶缺口：东方栀子缺 `chibi/expression`；`normal_proportion` 只剩 `cang_qiong` / `shi_an`，两者的 `chibi` 已由 Medium5 concept crop 清除，`expression` 已由五维介质 Bilibili 表情小批清除。
- 日V 4 人仍有至少一个分桶缺口；Lily 只缺 `expression`，猫村伊吕波缺 `chibi/expression`，HARU/MAYU 缺 `chibi`，不能判定日V分桶目标完成。

下一步仍应继续找精确官方或可信 wiki 文件来源；不能用盲搜、合并页双人图、社交图或截图凑数。
