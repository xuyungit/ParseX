# ParserX Evaluation Report

## Run Metadata

- Config source: `/Users/xuyun/Projects/ParserX/parserx.yaml`
- Overrides: `(none)`
- PDF provider: `pymupdf`
- DOCX provider: `docling`
- OCR builder: `paddleocr | model=PaddleOCR-VL-1.5 | lang=ch_sim+en`
- VLM service: `provider=openai | model=gpt-5.4-mini | api_style=auto | endpoint=http://74.211.103.125:3895/openai`
- LLM service: `provider=openai | model=gpt-5.4-mini | api_style=auto | endpoint=http://74.211.103.125:3895/openai`
- Image routing: `vlm_description=on | prompt=strict_auto | response=json | structured=json_schema | retry=1 | max_tokens=8192 | skip_large_text_overlap_chars=1200`
- Chapter fallback: `on`
- Verification: `hallucination=on | completeness=on | structure=on`

## Summary

- Documents: 16
- Avg edit distance: 0.232
- Avg char F1: 0.903
- Avg heading F1: 0.542
- Avg table cell F1: 0.488
- Total warnings: 42
- API calls (OCR/VLM/LLM): 32/49/9
- LLM fallback hits: 19
- Total wall time: 464.4s

## Per Document

| Document | Edit Dist | Char F1 | Heading F1 | Table F1 | Warn | API O/V/L | Fallback | Time |
|----------|-----------|---------|------------|----------|------|-----------|----------|------|
| deepseek | 0.077 | 0.939 | 0.000 | 0.000 | 0 | 0/0/0 | 0 | 0.8s |
| ocr01 | 0.139 | 0.960 | 0.737 | 0.903 | 0 | 4/1/1 | 4 | 48.4s |
| ocr_scan_jtg3362 | 0.239 | 0.884 | 0.095 | 0.714 | 16 | 4/6/1 | 3 | 41.6s |
| paper01 | 0.252 | 0.979 | 0.832 | 0.000 | 6 | 6/13/1 | 0 | 62.1s |
| paper_chn01 | 0.707 | 0.714 | 0.812 | 1.000 | 1 | 5/5/1 | 0 | 45.9s |
| paper_chn02 | 0.633 | 0.807 | 0.129 | 0.000 | 5 | 3/3/1 | 1 | 104.9s |
| patent01 | 0.425 | 0.908 | 0.500 | 1.000 | 2 | 2/14/1 | 1 | 51.7s |
| pdf_text01_tables | 0.041 | 0.979 | 0.000 | 0.992 | 0 | 0/0/0 | 0 | 32.0s |
| receipt | 0.089 | 0.958 | 1.000 | 0.000 | 2 | 1/2/0 | 0 | 1.8s |
| simple_doc01 | 0.711 | 0.458 | 0.286 | 0.000 | 10 | 0/0/1 | 2 | 4.5s |
| text_code_block | 0.050 | 0.975 | 0.500 | 0.000 | 0 | 0/0/0 | 0 | 0.5s |
| text_pic02 | 0.182 | 0.975 | 0.250 | 0.195 | 0 | 6/3/1 | 2 | 48.8s |
| text_report01 | 0.122 | 0.937 | 0.526 | 1.000 | 0 | 0/2/1 | 6 | 9.3s |
| text_table01 | 0.004 | 0.998 | 1.000 | 0.000 | 0 | 0/0/0 | 0 | 0.0s |
| text_table_libreoffice | 0.006 | 0.997 | 1.000 | 1.000 | 0 | 0/0/0 | 0 | 7.8s |
| text_table_word | 0.035 | 0.984 | 1.000 | 1.000 | 0 | 1/0/0 | 0 | 4.3s |
| **Average** | **0.232** | **0.903** | **0.542** | **0.488** | **42** | **32/49/9** | **19** | **464.4s** |

## Residual Themes

| Theme | Docs |
|-------|------|
| image_reference_markup | 4 |
| missing_table_shape | 4 |
| extra_heading | 3 |
| output_light | 3 |
| table_markup_shape | 3 |
| missing_heading | 1 |
| output_heavy | 1 |

## Warning Types

| Warning Type | Count |
|--------------|-------|
| Orphan heading | 27 |
| Low-confidence VLM | 6 |
| Duplicate body text | 5 |
| Heading level jump | 4 |

## Warning Hotspots

- ocr_scan_jtg3362: 16 warning(s) — Page 1: low-confidence VLM description (distance=0.78).; Page 1: low-confidence VLM description (distance=0.99).; ...
- simple_doc01: 10 warning(s) — Page 1: heading level jump from H2 to H4 (1.3.1).; Page 1: orphan H4 heading without H3 parent (1.3.1).; ...
- paper01: 6 warning(s) — Page 3: low-confidence VLM description (distance=0.87).; Page 5: low-confidence VLM description (distance=0.75).; ...
- paper_chn02: 5 warning(s) — Page 2: orphan H2 heading without H1 parent (1 板梁桥的结构模型修正).; Page 3: orphan H2 heading without H1 parent (对于 M 个试验工况, 令).; ...
- patent01: 2 warning(s) — Page 2: orphan H2 heading without H1 parent (1. 一种基于静载试验识别连续梁桥实际刚度的方法，其特征在于，包括如下步骤：).; Page 5: image description duplicates nearby body text (92% overlap).
- receipt: 2 warning(s) — Page 1: heading level jump from H1 to H3 (账单与付款).; Page 1: orphan H3 heading without H2 parent (账单与付款).
- paper_chn01: 1 warning(s) — Page 3: image description duplicates nearby body text (72% overlap).

## Residual Diagnostics

### simple_doc01

- Themes: `output_light, extra_heading, missing_heading`
- Blocks: extra=4, missing=4
- Output-only excerpt: `# 马来西亚槟城轻轨健康监测智慧支座
## 1 方案
### 1.1 智慧支座概述
#### 1.1.1
#### 1.1.2
#### 1.1.3
## 1.2
## 1.3
#### 1.3.1
#### 1.3.2
#### 1.3.3
#### 1.3.4
## 1.4
#### 1.4.1
#### 1.4.2
#### 1.4.3
#### 1.4.4
## 1.5
## 2 布设方案
## 2.1
## 2.2
## 2.3
# 3
## 4 设备实施与维护`
- Expected-only excerpt: `# 马来西亚槟城轻轨跨海段
# 桥梁健康监测系统—智慧支座 技术建议书
## 智慧支座技术方案
### 智慧支座概述
#### 概念
#### 功能
#### 应用前景（智慧桥梁和数字孪生等）
### 智慧支座系统要求
（对应TRS 3.2.1g条：BHMS Sensors reliability, maintenance and calibration requirement and interval, lifespan of the components and spare part availability）
### 智慧支座解决方案
介绍智慧支…`

### paper_chn01

- Themes: `output_light`
- Blocks: extra=12, missing=12
- Output-only excerpt: `（1）本文方法只需对中间支座的竖向反力增量进行测量。目前已有商用的特种支座（装备了应变
传感单元）可直接提供竖向支座反力数据。实桥的尺寸越大、限于条件可承受的测点数越少，则本文方
法的优势越突出。
（2）本文方法数据处理过程简单，计算量小。
（3）采用区间作为几何度量单位，便于分析和计算。虽然定位的直接结果是1对小区间，但结合检
测设备可方便地确定损伤情形。
（4）损伤定位的效果可通过改变荷载F和虚拟区间长度m的取值来进行调节。F值越大、m值越
小，则效果越好。
（5）本文的分析是在一维框架内进行的，后续研究可将分析扩展到二维情形，从而充分考虑桥梁的
…`
- Expected-only excerpt: `此时区间 $[x^{'}, x^{'} + m]$ 内具有与其他区间不同的局部刚度。虚拟分割节点处的 DIILSR 值从 $A \cdot (B - 3 l m)$ (即 $x_{2} < x^{'} + m$ 时) 到 $A (B - 2 l m)$ (即 $x_{2} = x^{'} + m$ 时), 再到 $A (B + 6 l x^{'})$ (即 $x_{2} > x^{'} + m$ 时)。其中
$$ A = [F m^{2} (z^{'} - 1) 12 z^{'} ] [12 (x^{'})^{3} l (z^{'})^{2} + 3 …`

### paper_chn02

- Themes: `output_light`
- Blocks: extra=12, missing=12
- Output-only excerpt: `为叙述方便，称本文方法为方法A，文献[18] 的方法为方法B.为比较2种方法的适用性，分别对大损伤、微损伤和无损伤 3种情况的模型修正结果进行比较，均采用表 2中所有 3个工况的数据分析 .针对 3种损伤情况，分别采用方法A和方法B 反推 3种情况下板的修正系数和铰缝刚度 .对于铰缝刚度，2种方法均能获得，见表 5；对于板的修正系数，只有用方法A才能获得，见表 6.
表 5中，对于每种损伤情况均列出了 3组计算结果 .第 1组和第 2组分别为方法A和方法B获得的铰缝刚度的误差（由于防撞护栏对边板刚度的影响难以估计，计算时未考虑防撞护栏的影响）；第 3组…`
- Expected-only excerpt: `为叙述方便, 称本文方法为方法 A, 文献[18]的方法为方法 B。为比较 2 种方法的适用性, 分别对大损伤、微损伤和无损伤 3 种情况的模型修正结果进行比较, 均采用表 2 中所有 3 个工况的数据分析。针对 3 种损伤情况, 分别采用方法 A 和方法 B 反推 3 种情况下板的修正系数和铰缝刚度。对于铰缝刚度, 2 种方法均能获得, 见表 5; 对于板的修正系数, 只有用方法 A 才能获得, 见表 6。
表 5 中, 对于每种损伤情况均列出了 3 组计算结果。第 1 组和第 2 组分别为方法 A 和方法 B 获得的铰缝刚度的误差 (由于防撞护栏对…`

### patent01

- Themes: `(none)`
- Blocks: extra=29, missing=28
- Output-only excerpt: `本发明涉及一种基于静载试验识别连续梁桥实际刚度的方法，包括步骤：依照荷载试验加载工况建立桥梁有限元计算模型；计算结构位移，将位移向量按照测量值和未测量值分开，构建目标函数；选择全部单元刚度作为待识别参数；采用遗传算法，设置待识别参数的上下限进行初步优化计算；将遗传算法计算结果作为初始值，采用L-M算法进行第二轮优化计算；根据测点布置结合灵敏度分析判断刚度识别的有效区域。
本发明依据静载试验数据进行有限元模型修正，不需要额外布置测点和加载方案；不需要进行有
CN 106844965 B
限元模型缩聚，可避免矩阵奇异；选用全部单元刚度作为待识别参数，可避免…`
- Expected-only excerpt: `1. 一种基于静载试验识别连续梁桥实际刚度的方法，其特征在于，包括如下步骤：
步骤一：依照荷载试验加载工况建立桥梁有限元计算模型；
步骤二：计算结构位移，将位移向量按照测量值和未测量值分开，构建目标函数；
步骤三：选择全部单元刚度作为待识别参数；
步骤四：采用遗传算法，设置待识别参数的上下限进行初步优化计算；
步骤五：将遗传算法计算结果作为初始值，采用L-M算法进行第二轮优化计算；
步骤六：根据测点布置结合灵敏度分析判断刚度识别的有效区域；
步骤一中，桥梁有限元计算模型的力学方程为：
$$[k][\delta]=[F] \quad (1)$$
式中[k…`

### paper01

- Themes: `(none)`
- Blocks: extra=43, missing=48
- Output-only excerpt: `Dataflow
Architectures, pages
225–253.
1986. www.dtic.mil/cgi-bin/GetTRDoc?Location=U2& doc=GetTRDoc.pdf&AD=ADA166235.
[4] Arvind and Rishiyur S. Nikhil. Executing a pro-
gram on the MIT tagged-token dataflow architec-
*IEEE Trans. Comput.*, 39(3):300–318, 1990. ture.
dl.acm.org…`
- Expected-only excerpt: `---
Ke Yang, and Andrew Y. Ng. Large scale distributed deep networks. In *NIPS*, 2012. Google Research PDF.
[15] Jack J Dongarra, Jeremy Du Croz, Sven Hammarling, and Iain S Duff. A set of level 3 basic linear algebra subprograms. *ACM Transactions on Mathematical Software (TOMS…`

### ocr_scan_jtg3362

- Themes: `image_reference_markup, extra_heading, missing_table_shape`
- Blocks: extra=12, missing=12
- Output-only excerpt: `> [图片] A simple line diagram with intersecting angled lines and a curved line.
### 公路钢筋混凝土及预应力
### 混凝土桥涵设计规范
> [图片] JTG 3362—2018
Specifications for Design of Highway Reinforced Concrete
and Prestressed Concrete Bridges and Culverts
> [图片] A very faint, low-contrast image with s…`
- Expected-only excerpt: `| 消除应力钢丝 | 1470 | 1000 |  |
|  | 1570 | 1070 | 410 |
|  | 1770 | 1200 |  |
|  | 1860 | 1260 |  |
| 预应力螺纹钢筋 | 785 | 650 |  |
|  | 930 | 770 | 400 |
|  | 1080 | 900 |  |
3.2.4 普通钢筋的弹性模量E_s和预应力钢筋的弹性模量E_p宜按表3.2.4采用；当有可靠试验依据时，E_s和E_p可按实测数据确定。
**表 3.2.4 钢筋的弹性模量**
| 钢筋种类 | 弹性模量E_s(×10^…`

### text_pic02

- Themes: `image_reference_markup, table_markup_shape, extra_heading, missing_table_shape`
- Blocks: extra=9, missing=10
- Output-only excerpt: `> [图片] A corporate logo with stylized green and blue diagonal shapes above Chinese and English text.
# 佳讯飞鸿智能科技研究院监测云裸金属服务配置指南
V01
| 拟制人： | 徐蕴 | 日期： | 2022-05-30 |
| --- | --- | --- | --- |
| 审核人： | 赵文祥 | 日期： | 2022-05-30 |
| 批准人： | 张鑫 | 日期： | 2022-05-30 |
本文档是佳讯飞鸿（北京）智能科技研究院有限公…`
- Expected-only excerpt: `|  |  |
|---|---|
| 佳讯⻜鸿智能科技研究院 监测云裸⾦属服务配置指南 V01 拟制⼈： 徐蕴 ⽇期： 2022-05-30 审核⼈： 赵文祥 ⽇期： 2022-05-30 批准⼈： 张鑫 ⽇期： 2022-05-30 |  |
| 本⽂档是佳讯⻜鸿（北京）智能科技研究院有限公司的内部⽂档，⽂档的版权属于佳讯⻜鸿（北京）智 能科技研究院有限公司。任何使⽤、复制、公开此⽂档的⾏为都必须经过佳讯⻜鸿（北京）智能科技研 究院有限公司的书⾯允许。 |  |
|  |  |
> [图片] 佳讯飞鸿
| 版本 |  |  | 修改内容 |  | …`

### ocr01

- Themes: `table_markup_shape, missing_table_shape`
- Blocks: extra=14, missing=17
- Output-only excerpt: `|  | 辛伐他汀 | 服用Paxlovid前，需确认已停用洛美他派满12小时；使用Paxlovid期间停用洛美他派；停用Paxlovid三天后可用回洛美他派 |
|  | 洛美他派 | 雷诺嗪 |
|  | 胺碘酮 | 卡普地尔 |
|  | 决奈达隆 | 禁忌！ |
| 心脏疾病 | 恩卡尼 | 不能用Paxlovid，请选择其他抗病毒药 |
|  | 氟卡尼 | 普罗帕酮 |
|  | 奎尼丁 | 卡普地尔 |
| 肿瘤 | 奈拉替尼 | 禁忌!不能用Paxlovid,请选择其他抗病毒药 |`
- Expected-only excerpt: `|  | 辛伐他汀 |  |
| 心脏疾病 | 洛美他派 | 服用Paxlovid前，需确认已停用洛美他派满12小时；使用Paxlovid期间停用洛美他派；停用Paxlovid三天后可用回洛美他派 |
|  | 雷诺嗪 |  |
|  | 胺碘酮 |  |
|  | 苄普地尔 |  |
|  | 决奈达隆 | 禁忌！不能用Paxlovid,请选择其他抗病毒药 |
|  | 恩卡尼 |  |
|  | 氟卡尼 |  |
|  | 普罗帕酮 |  |
|  | 奎尼丁 |  |
| 肿瘤 | 奈拉替尼 | 禁忌! 不能用Paxlovid,请选择其他抗病毒药…`

### text_report01

- Themes: `image_reference_markup`
- Blocks: extra=4, missing=4
- Output-only excerpt: `3.参考以下图示装订方式，采用“骑马钉”方式装订成册。
> [图片] 显示了多本文件夹/册子并排摆放，颜色包括黑色、绿色、粉色和白色。
> [图片] 示意图展示了装订成册的方式，箭头指向左侧边缘的两处装订位置。`
- Expected-only excerpt: `- **重大技术装备首台套/软件首版次名称**：
- **产品类别**：（选择首台套或首版次）
- **申报单位**：（公章）
- **日期**：年 月`

### receipt

- Themes: `output_heavy, image_reference_markup`
- Blocks: extra=5, missing=4
- Output-only excerpt: `> [图片] A rounded wallet or card-holder icon on a dark background, with layered colored bands near the top and a red center tab.
Apply and use in minutes<sup>2</sup>
1. 3% savings is earned as Daily Cash and is the total amount of Daily Cash earned for these purchases. You can ch…`
- Expected-only excerpt: `> [图片] A stylized wallet or card-holder icon on a dark background.
Apply and use in minutes2
1. 3% savings is earned as Daily Cash and is the total amount of Daily Cash earned
for these purchases. You can choose to direct Daily Cash to a Savings account or to an Apple Cash accou…`

### deepseek

- Themes: `(none)`
- Blocks: extra=4, missing=4
- Output-only excerpt: `其它（如市场份额、⽤户增⻓等）
此外，你希望关注的时间范围是近期（如最近⼏周）还是更⻓期的趋势？
包括你提到的所有⽅⾯。我希望是看到今年以来的发⽣的真实变化，或者能够看到的趋势
我会研究 DeepSeek R1/V3 发布后对国内外⼤模型公司的影响，包括：`
- Expected-only excerpt: `其它（如市场份额、用户增长等）
此外，你希望关注的时间范围是近期（如最近几周）还是更长期的趋势？
包括你提到的所有方面。我希望是看到今年以来的发生的真实变化，或者能够看到的趋势
我会研究DeepSeek R1/V3发布后对国内外大模型公司的影响，包括：`

### text_code_block

- Themes: `(none)`
- Blocks: extra=11, missing=11
- Output-only excerpt: `kolla-ansible/tools/kolla-ansible -i multinode --configdir . --passwords
passwords.yml --tag ceph -- limit control,storage03 deploy
```
13. 由于使⽤8T的盘替换6T的盘，因此调整OSD的权重
````
- Expected-only excerpt: `12. 执行补充部署，使用kolla-ansible工具完成部署，参考命令，其中storage02节点名替换为故障OSD所在的节点名称
```bash
kolla-ansible/tools/kolla-ansible -i multinode --configdir . --passwords passwords.yml --tag ceph -- limit control,storage03 deploy`

### pdf_text01_tables

- Themes: `table_markup_shape, missing_table_shape`
- Blocks: extra=7, missing=7
- Output-only excerpt: `中铁七局集团有限公司无锡至太仓高速公路 XTC-XS3 标项目工程锚具、桥梁伸缩缝、支座采购竞争性谈判物资需求一览表
工程项目名称：中铁七局集团有限公司无锡至太仓高速公路 XTC-XS3 标项目谈判编号：XTGCTP2026-006
| 序号 | 包件号 | 物资名称 | 规格型号 | 计量 单位 | 数量 | 计划交货日期 | 谈判保证金 (元) | 交货地点 |`
- Expected-only excerpt: `中铁七局集团有限公司无锡至太仓高速公路XTC-XS3标项目工程
锚具、桥梁伸缩缝、支座采购竞争性谈判物资需求一览表
工程项目名称：中铁七局集团有限公司无锡至太仓高速公路XTC-XS3标项目
谈判编号：XTGCTP2026-006
| 序号 | 包件号 | 物资名称 | 规格型号 | 计量单位 | 数量 | 计划交货日期 | 谈判保证金(元) | 交货地点 |`

### text_table_word

- Themes: `(none)`
- Blocks: extra=3, missing=4
- Output-only excerpt: `2.项目组依据科研任务书，在对以北京地铁工务规章制度、业务流程等为核心的知识要素深入调研系统分析基础上，形成了
工务专业知识库构建方法，构建了工务专业知识库体系，攻克了面向工务知识问答智能体和大模型构建技术，提出了专业知识系
统集成方案，研制开发完成了工务专业知识问答助手技术平台，并进行了现场部署测试应用。`
- Expected-only excerpt: `2.项目组依据科研任务书，在对以北京地铁工务规章制度、业务流程等为核心的知识要素深入调研系统分析基础上，形成了工务专业知识库构建方法，构建了工务专业知识库体系，攻克了面向工务知识问答智能体和大模型构建技术，提出了专业知识系统集成方案，研制开发完成了工务专业知识问答助手技术平台，并进行了现场部署测试应用。`

### text_table_libreoffice

- Themes: `(none)`
- Blocks: extra=2, missing=2
- Output-only excerpt: `2.项目组依据科研任务书，在对以北京地铁工务规章制度、业务流程等为核心的知识要素深入调研系统分析基础上，形成了
工务专业知识库构建方法，构建了工务专业知识库体系，攻克了面向工务知识问答智能体和大模型构建技术，提出了专业知识系
统集成方案，研制开发完成了工务专业知识问答助手技术平台，并进行了现场部署测试应用。`
- Expected-only excerpt: `2.项目组依据科研任务书，在对以北京地铁工务规章制度、业务流程等为核心的知识要素深入调研系统分析基础上，形成了工务专业知识库构建方法，构建了工务专业知识库体系，攻克了面向工务知识问答智能体和大模型构建技术，提出了专业知识系统集成方案，研制开发完成了工务专业知识问答助手技术平台，并进行了现场部署测试应用。`

### text_table01

- Themes: `(none)`
- Blocks: extra=2, missing=2
- Output-only excerpt: `1、产品质量外观很重要，首先要把模具内腔，上下盖模要处理好，发现模具损伤严重的要及时返修，来年生产中每天都需要关注的问题。完成时间需要
领导们的配合。`
- Expected-only excerpt: `1、产品质量外观很重要，首先要把模具内腔，上下盖模要处理好，发现模具损伤严重的要及时返修，来年生产中每天都需要关注的问题。完成时间需要领导们的配合。`

