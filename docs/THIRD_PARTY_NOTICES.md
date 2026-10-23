# 资料、依赖与参考来源

## 内置资料
本地演示只收录公开页面/公告中的短摘录及结构化字段，没有打包完整年报、整篇网页或付费研报。原内容权利归原发布方；出处如下，核查应返回原文。开发时核对记录不代表独立交叉验证。

| ID | 发布方 | 资料 |
| --- | --- | --- |
| S01 | 工信部（数据来自中汽协） | [2024年12月汽车工业经济运行情况](https://www.miit.gov.cn/gxsj/tjfx/zbgy/qc/art/2025/art_a8895b6aa6d449e2861d027b0d85a2dd.html) |
| S02 | 比亚迪 | [比亚迪2024年销量公告](https://prod2.byd.com/cn/news/2025/detail560) |
| S03 | 理想汽车投资者关系 | [理想2024全年业绩（未经审计）](https://ir.lixiang.com/news-releases/news-release-details/li-auto-inc-announces-unaudited-fourth-quarter-and-full-year-3) |
| S04 | 蔚来投资者关系 | [蔚来2024全年业绩（未经审计）](https://ir.nio.com/news-releases/news-release-details/nio-inc-reports-unaudited-fourth-quarter-and-full-year-2024) |
| S05 | 宁德时代 | [神行PLUS电池发布](https://www.catl.com/news/7945.html) |
| S06 | 蔚来投资者关系 | [蔚来2024全年交付与新品发布](https://ir.nio.com/news-releases/news-release-details/nio-inc-provides-december-fourth-quarter-and-full-year-2024) |
| S07 | 中国汽车流通协会乘联分会 | [2024年12月份全国乘用车市场分析](https://www.cada.cn/Trends/info_91_10118.html) |
| S08 | CnEVPost（转引乘联会CPCA） | [2023年新能源厂商零售份额（转引乘联会）](https://cnevpost.com/2024/01/23/wuling-aims-700000-nev-sales-2024/) |
| S09 | CnEVPost（转引乘联会CPCA） | [2024年新能源厂商零售份额（转引乘联会）](https://cnevpost.com/2025/01/10/automakers-share-china-nev-market-2024/) |
| S10 | 比亚迪官方媒体中心 | [比亚迪2024财务业绩（官方发布）](https://media.byd.com/byd-presente-ses-resultats-financiers-2024-les-recettes-atteignent-7771-milliards-de-yuans-en-hausse-de-23-par-rapport-a-lannee-precedente/?lang=fra) |


conflict_overlay.json 是程序主动注入的合成冲突测试夹具，带 synthetic_test 标记，不是现实披露数据，也不得成为正式报告依据。

联网报告示例保留对应来源链接；截图展示真实界面及公开资料，没有把网页全文收进仓库。示例的未来计划、预计值和事后观察应按原期间解读。

## 第三方组件
核心使用 Python 标准库和原生浏览器技术。PDF 功能可选安装 [PyMuPDF 1.28.2](https://pypi.org/project/PyMuPDF/1.28.2/)，由 Artifex 维护，按 AGPL / 商业许可提供，详见[官方许可文件](https://github.com/pymupdf/PyMuPDF/blob/master/COPYING)。本运行包不捆绑 PyMuPDF、Python 或浏览器二进制。

## 接口与方案参考
服务商接口通过各自官方域名调用；地址与协议见 agent_demo/live_agent.py，OpenAI 本地模式见 model_review.py。服务商产品与名称归各自权利人。

模型目录组织思路参考：
- [Cherry Studio：listModels.ts](https://github.com/CherryHQ/cherry-studio/blob/main/src/main/ai/provider/listModels.ts)
- [Open WebUI：models.py](https://github.com/open-webui/open-webui/blob/main/backend/open_webui/utils/models.py)

此处仅参考组织方式，未复制上述项目源码或引入其依赖。

## 本项目代码
本仓库用于公开展示、阅读和本机运行；当前未另行指定项目源码的开源许可证。第三方资料、组件和接口不因出现在本项目中而被重新授权。
