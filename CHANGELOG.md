# Changelog

## v1.1.0 (2026-11-07)

- 新增 `/ascii @成员` 与 `/ascii QQ号`：抓取 QQ 头像后再转换；自动排除 `@全体成员` 与 `@机器人自己`。
- 新增单发 `/ascii`：回溯同一会话内该用户最近 60 秒发过的一张图片，时间窗口可在 WebUI 配置。
- 新增字符画比例修正：按字体 `getbbox()` 动态计算上游约 1.6 倍的横向拉伸并预拉伸输入图，输出保持原生字符大小。
- `keep_original_size` 改为默认关闭，避免小图上字符被压糊；`fix_aspect_ratio` 默认开启。
- 图片统一以字节 + `Image.fromBytes()` 回发，兼容 NapCat 与 AstrBot 分属不同容器的部署。
- 结果读图、暂存、转换增加超时、并发锁、固定输出路径与失败提示。
- `_conf_schema.json` 补齐至 9 项，全部可在 AstrBot WebUI 配置。

## v1.0.0

- 首个版本：图片 + `/ascii` / `/字符画` 转字符画，支持彩色、白底、列数、超时与生成器路径。
- 增加 `_conf_schema.json`，插件配置出现在 AstrBot WebUI。
- 增加「把输出缩回原图像素尺寸」开关。
