# Bang Dream! 系列角色包录入准备

> 2026-06-02。用于后续在 Admin「角色识别 → 录入角色 → 系列 pack」中构建 `bangdream.charpack`。

## 范围

首批按 BanG Dream! 官方日文站 Artist 页当前列出的 12 个主线团处理，共 60 个角色：

| 团体 | 角色数 | character_id |
|---|---:|---|
| Poppin'Party | 5 | `toyama_kasumi`, `hanazono_tae`, `ushigome_rimi`, `yamabuki_saya`, `ichigaya_arisa` |
| Afterglow | 5 | `mitake_ran`, `aoba_moca`, `uehara_himari`, `udagawa_tomoe`, `hazawa_tsugumi` |
| Pastel＊Palettes | 5 | `maruyama_aya`, `hikawa_hina`, `shirasagi_chisato`, `yamato_maya`, `wakamiya_eve` |
| Roselia | 5 | `minato_yukina`, `hikawa_sayo`, `imai_lisa`, `udagawa_ako`, `shirokane_rinko` |
| ハロー、ハッピーワールド！ | 5 | `tsurumaki_kokoro`, `seta_kaoru`, `kitazawa_hagumi`, `matsubara_kanon`, `okusawa_misaki` |
| Morfonica | 5 | `kurata_mashiro`, `kirigaya_toko`, `hiromachi_nanami`, `futaba_tsukushi`, `yashio_rui` |
| RAISE A SUILEN | 5 | `wakana_rei`, `asahi_rokka`, `sato_masuki`, `nyubara_reona`, `tamade_chiyu` |
| MyGO!!!!! | 5 | `takamatsu_tomori`, `chihaya_anon`, `kaname_rana`, `nagasaki_soyo`, `shiina_taki` |
| Ave Mujica | 5 | `misumi_uika`, `wakaba_mutsumi`, `yahata_umiri`, `yutenji_nyamu`, `togawa_sakiko` |
| 夢限大みゅーたいぷ | 5 | `nakamachi_arale`, `miyanaga_nonoka`, `minetsuki_ritsu`, `fuji_miyako`, `sengoku_yuno` |
| millsage | 5 | `shiomi_hotaru`, `izawa_natsume`, `kotohira_nagi`, `hamasaki_mahoro`, `izumi_houka` |
| 一家Dumb Rock! | 5 | `suga_raika`, `mahashi_miku`, `yakura_yomogi`, `umezato_chieri`, `shinomiya_shizuku` |

暂不把非 Artist 页主线团体或动画副角色作为首批录入对象，例如 Glitter*Green、CHiSPA、sumimi、CRYCHIC 独立时期等；其中 CRYCHIC 多数成员已被 MyGO!!!!! / Ave Mujica 覆盖。后续如果聊天场景需要再补二期包或 aliases。

## Admin 录入字段

- `pack_name`: `bangdream`
- `series`: `bangdream`
- `work`: `BanG Dream!`
- `relation_default`: `known`
- `characters_json`: 直接使用 [bangdream-characters.json](bangdream-characters.json)

图片按 `file_prefix || character_id` 归组。当前 JSON 未显式写 `file_prefix`，因此文件名使用：

```text
toyama_kasumi_01.jpg
toyama_kasumi_02.jpg
...
togawa_sakiko_01.jpg
```

## 采样建议

- 每个角色先准备 8-12 张正脸或半身图，避免同一卡面近似裁剪重复太多；60 人首批约 480-720 张。
- 尽量使用官方立绘、卡面、动画截图；不要混入 fan art、cosplay、真人声优照。
- RAISE A SUILEN 与 Ave Mujica 的 `character_id` 使用角色本名，`aliases` 保留舞台名；图片可以包含舞台装扮。
- `okusawa_misaki` 同时承载奥沢美咲与ミッシェル。Michelle 外形差异很大，首批建议同时收集美咲人形和 Michelle 头套形态；如果识别效果差，再单独拆 `okusawa_misaki_michelle` 多形态补丁。
- 梦限大、millsage、一家Dumb Rock! 官方图源相对少，先保持每人图量均衡，避免新团角色被少样本拖低置信。

## 来源与核对

- 官方日文 Artist 页：<https://bang-dream.com/artist/>。页面当前列出 12 团入口，并在各团页面列出成员 slug 与日文名。
- 官方英文 Artists 页：<https://en.bang-dream.com/artists/>。英文页当前覆盖到 Ave Mujica，可用于交叉核对旧 9 团英文团名。

本表大多数条目用官方 slug 转 snake_case 作为 `character_id`；RAISE A SUILEN 与 Michelle 这类舞台名/形态名条目改成本名型稳定 ID，舞台名保留在 `name` 或 `aliases`。日文官方名作为 `name`，中文常用名、罗马字、舞台名写入 `aliases`。
