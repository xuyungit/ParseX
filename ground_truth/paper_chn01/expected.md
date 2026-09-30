第 27 卷 第 4 期 中 国 公 路 学 报 Vol. 27 No. 4
2014 年 4 月 China Journal of Highway and Transport Apr. 2014

文章编号: 1001-7372(2014)04-0079-06

# 基于竖向支座反力的两跨连续梁桥损伤定位方法


王艺霖<sup>1</sup>, 张 平<sup>2</sup>, 安新梅<sup>3</sup>

(1. 山东建筑大学 土木工程学院, 山东 济南 250101; 2. 中国十七冶集团有限公司, 安徽 马鞍山 243000; 3. 南洋理工大学 土木与环境工程学院, 新加坡 639798)

**摘要**: 为了实现连续梁桥主梁的准确损伤定位, 通过对两跨连续梁桥进行影响线分析, 基于“支座反力影响线二次差值”的概念提出了一个新的损伤定位指标, 即竖向支座反力影响线差值指标 (DIILSR), 并基于 DIILSR 建立了相应的损伤定位方法。研究结果表明: 对于局部抗弯刚度具有不确定性的实际主梁, 可通过对比损伤前后的 DIILSR 数据图形来有效识别损伤位置; 损伤定位的效果可通过改变荷载的大小和虚拟区间的长度来调节。

**关键词**: 桥梁工程; 连续梁桥; 影响线分析; 损伤定位

**中图分类号**: U441.4 **文献标志码**: A

## Two-span Continuous Bridge Damage Localization Method Based on Vertical Support Reaction


WANG Yi-lin<sup>1</sup>, ZHANG Ping<sup>2</sup>, AN Xin-mei<sup>3</sup>

(1. School of Civil Engineering, Shandong Jianzhu University, Jinan 250101, Shandong, China; 2. China MCC 17 Group Co., Ltd., Ma'anshan 243000, Anhui, China; 3. School of Civil and Environmental Engineering, Nanyang Technological University, Singapore 639798, Singapore)

**Abstract**: To localize the damage to main beams of continuous bridges accurately, a novel damage localization index based on the idea of "second order difference of support reaction influence line" was presented by employing influence line analysis for two-span continuous bridges. This index was called "difference index of influence line for vertical support reaction" (DIILSR). Subsequently, the damage localization method based on DIILSR was established. The results show that damage position of main beams with uncertain local flexural stiffness can be effectively identified by comparing two DIILSR graphs before and after damage. The effect of damage localization can be adjusted by changing the load value and the length of virtual interval.

**Key words**: bridge engineering; continuous bridge; influence line analysis; damage localization

## 0 引 言

为了确保两跨连续梁桥的安全运行, 需要开发可靠的损伤定位技术来及时发现主梁上可能出现的损伤。目前主要有两大类损伤定位方法, 第 1 类方法是基于结构的动力性能指标 (如自振频率<sup>[1]</sup>、振型<sup>[2]</sup>、阻尼<sup>[3]</sup>、曲率模态<sup>[4]</sup>、应变模态<sup>[5]</sup>、频率响应函数<sup>[6]</sup> 等), 并取得了一定的实际应用效果, 但存在对

收稿日期: 2013-05-17
基金项目: 山东建筑大学博士科研基金项目 (XNBS1205)
作者简介: 王艺霖 (1981-), 男, 河南项城人, 讲师, 工学博士, E-mail: xgwang_wang@163.com。

测点数量的要求高、传感器精度有限、小损伤易被误差掩盖<sup>[7-8]</sup>、会受到环境温度变化的干扰<sup>[9]</sup>等缺陷。第 2 类方法是基于结构的静力位移响应。如：部分特征结构分配法将损伤识别处理成优化问题来求解损伤参数<sup>[10]</sup></mark>；Yeo 等<sup>[11]</sup>和 Jang 等<sup>[12]</sup>采用非线性正则化和自适应子结构方法修正结构模型，引入假设检验的方法来识别损伤。这类方法对测点的数量要求也很高，影响到了实际应用的效果。

另外需要注意到，由于材料特点和构件尺寸的施工误差，实际中连续梁桥主梁上各处的弹性模量和惯性矩有一定的离散性，因此局部抗弯刚度的分布也具有不确定性。而损伤在理论上一般都用局部抗弯刚度的变化来表征<sup>[3,13]</sup>，因此这一效应会直接影响损伤识别，对理论方法所识别的局部刚度变化必须区分出是由真实损伤造成的还是初始不确定性造成的。
目前对不确定性的研究多基于统计分析<sup>[14]</sup>和模糊逻辑<sup>[15-16]</sup>。但前者依赖于概率假定的准确性，而且对损伤不太敏感，后者只能对损伤程度进行分级定量。因此，有必要继续开发能合理处理局部刚度初始不确定性的损伤定位方法。

针对实际两跨连续梁桥的主梁，本文中基于竖向支座反力的测量和支座反力影响线二次差值的概念建立一个新型损伤定位指标——竖向支座反力影响线差值指标</mark>，并基于该指标建立相应的损伤定位方法。

## 1 抗弯刚度初始不确定性

对主梁上损伤位置的描述需要 2 个参数：损伤区域到桥梁某端的距离 $x$，损伤区域的长度 $y$。为了简化，可将主梁沿长度方向虚拟分割为若干个等长的区间（长度记为 $m$），则损伤区域 $y$ 就可划分为若干个 $m$。某处损伤对应的 $x$ 修正为 $x'$，进行损伤定位时只需求解参数 $x'$ 即可。

对某两跨连续梁桥，3 个支座分别记为 $a, b, c$。首先根据虚拟分割的概念将其主梁划分为若干个长度为 $m$ 的区间，然后让集中荷载 $F$ 在主梁上移动，关注当 $F$ 出现在各虚拟分割的节点处时，中间支座 $b$ 的竖向反力增加值的变化量。用参数 $x_1$ 代表施加荷载节点到主梁左端的距离。

为考虑主梁上局部抗弯刚度的初始不确定性，先假设只有一个区间的等效抗弯刚度与其他区间都不同，其他各区间的局部抗弯刚度记为 $EI$（$E$ 为主梁所用混凝土的弹性模量，$I$ 为主梁截面的惯性矩）。这一特殊区间的等效抗弯刚度记为 $z'EI$（其中 $z'$ 为待定参数）。特殊区间左端到桥梁左端的距离记为 $x'$，如图 1 所示。

![Analysis Model of Original Bridge](page_2_chart_1_v2.jpg)
图 1 完好桥梁的分析模型
**Fig. 1 Analysis Model of Original Bridge**

对于图 1 所示的超静定结构，宜采用力法求解。当 $F$ 出现在 $x_1 = x'$ 时，以支座 $b$ 的竖向反力增加量 $\Delta q_2^{x'}$ 作为基本未知量，可建立如下基本方程

$\Delta_{1p} + \Delta q_2^{x'} \delta_{11} = 0$ (1)

式中：$\Delta_{1p}$ 为图 1 的基本结构在 $F$ 作用下沿支座 $b$ 处竖直方向的位移；$\delta_{11}$ 为比例系数。

基于虚功原理可以得到如下方程

$$
\begin{aligned}
EI \delta_{11} = & \frac{(x')^3}{12} + \frac{l^3}{48} - \frac{(x' + m)^3}{12} + \\
& \frac{(x' + m)^3 - (x')^3}{12 z'}
\end{aligned}
$$ (2)

$$
\begin{aligned}
-EI \Delta_{1p} = & [ \frac{F l^2 x'}{16} - \frac{F x' (m + x')^2}{4} + \\
& \frac{F x' (m + x')^3 + F (x')^3 (l - x')}{6l} ] - \\
& \frac{F m x' [2m^2 + 6mx' - 3lm + 6(x')^2 - 6lx']}{12lz'}
\end{aligned}
$$ (3)

式中：$l$ 为主梁的总长。

然后可求出 $\Delta q_2^{x'}$，即

$$
\begin{aligned}
\Delta q_2^{x'} = & \{ z' [ \frac{F l^2 x'}{16} - \frac{F x' (m + x')^2}{4} + \\
& \frac{F x' (m + x')^3 + F (x')^3 (l - x')}{6l} ] - \\
& \frac{F m x' [2m^2 + 6mx' - 3lm + 6(x')^2 - 6lx']}{12l} \} \cdot \\
& \{ z' [ \frac{(x')^3}{12} + \frac{l^3}{48} - \frac{(x' + m)^3}{12} ] + \\
& \frac{(x' + m)^3 - (x')^3}{12} \}^{-1}
\end{aligned}
$$ (4)

由于主梁一般都不为深梁，因此这里忽略了剪切变形，下同。

当 $F$ 出现在对称位置 $x_1 = l - x'$ 时，支座 $b$ 的竖向反力增加量记为 $\overline{\Delta q}_2^{x'}$，即

$$
\begin{aligned}
\overline{\Delta q}_2^{x'} = & \{ z' [ \frac{F (x')^3 l}{6l} + F x' (3l^3 - 12l(x')^2 - 8m^3 - \\
& 24m^2 x' - 24m(x')^2) (48l)^{-1} ] + \\
& \frac{F m x' [m^2 + 3mx' + 3(x')^2]}{6l} \} \{ z' [ \frac{(x')^3}{12} + \frac{l^3}{48} -
\end{aligned}
$$

$$ \frac{(x' + m)^3}{12} ] + [ \frac{(x' + m)^3 - (x')^3}{12} ] \}^{-1} \tag{5} $$

记 $\dot{\Delta} q_2^{x'}$ 和 $\bar{\Delta} q_2^{x'}$ 的差值为 $\dot{\Delta} q_2^{x'}$, 则

$$ \begin{aligned} \dot{\Delta} q_2^{x'} = & \dot{\Delta} q_2^{x'} - \bar{\Delta} q_2^{x'} = F m x' (z' - 1) [ 4m^2 + \\ & 12 m x' - 3 l m + 12 (x')^2 - 6 l x' ] \cdot \\ & (12 l z' E I \delta_{11})^{-1} \end{aligned} \tag{6} $$

当 $F$ 出现在 $x_1 = x' - m, x_1 = x' - 2m, x_1 = x' - 3m$ 及对称位置时, 可以类似地得到 $\dot{\Delta} q_2^{x' - m}, \dot{\Delta} q_2^{x' - 2m}, \dot{\Delta} q_2^{x' - 3m}$。进而可发现以下规律

$$ \begin{aligned} \dot{\Delta} q_2^{x'} - \dot{\Delta} q_2^{x' - m} = & \dot{\Delta} q_2^{x' - m} - \dot{\Delta} q_2^{x' - 2m} = \dot{\Delta} q_2^{x' - 2m} - \\ & \dot{\Delta} q_2^{x' - 3m} = F m^2 (z' - 1) [ 4m^2 + 12 m x' - \\ & 3 l m + 12 (x')^2 - 6 l x' ] (12 l z' E I \delta_{11})^{-1} \end{aligned} \tag{7} $$

考虑损伤所在区间的右侧, 当 $F$ 出现在 $x_1 = x' + m$ 及对称位置时, 可求出 $\dot{\Delta} q_2^{x' + m}$ 为

$$ \begin{aligned} \dot{\Delta} q_2^{x' + m} = & F m (z' - 1) [ m^2 + 3 m x' + 3 (x')^2 ] (2 m - \\ & l + 2 x') (6 l z' E I \delta_{11})^{-1} \end{aligned} \tag{8} $$

可得

$$ \begin{aligned} \dot{\Delta} q_2^{x' + m} - \dot{\Delta} q_2^{x'} = & F m^2 (z' - 1) [ 4 m^2 + 12 m x' - \\ & 2 l m + 12 (x')^2 - 3 l x' ] (12 l z' E I \delta_{11})^{-1} \end{aligned} \tag{9} $$

对比式(7), (9)可得
$$ \begin{aligned} (\dot{\Delta} q_2^{x' + m} - \dot{\Delta} q_2^{x'}) - & (\dot{\Delta} q_2^{x'} - \dot{\Delta} q_2^{x' - m}) = \\ & F m^2 (z' - 1) (m + 3 x') \{ z' [ (x')^3 + \frac{l^3}{4} - (x' + \\ & m)^3 ] + (x' + m)^3 - (x')^3 \}^{-1} \end{aligned} \tag{10} $$

至此, 可定义一个指标 $\Delta_{x_1} = \dot{\Delta} q_2^{x_1} - \dot{\Delta} q_2^{x_1 - m}$。根据以上结果可知: 随着荷载 $F$ 的移动和 $x_1$ 的增加, $\Delta_{x_1}$ 的值刚开始保持不变, 当经过刚度不同的区间时会发生突变。这个指标可称为竖向支座反力影响线差值指标(DIILSR)。

当 $F$ 出现在 $x_1 = x' + 2m, x_1 = x' + 3m, x_1 = x' + 4m$ 及对称位置时, 可以类似地得到 $\dot{\Delta} q_2^{x' + 2m}, \dot{\Delta} q_2^{x' + 3m}, \dot{\Delta} q_2^{x' + 4m}$。可发现以下规律

$$ \begin{aligned} \Delta_{x_1 = x' + 2m} = & \Delta_{x_1 = x' + 3m} = \Delta_{x_1 = x' + 4m} = \\ & 4 F m^2 (z' - 1) [ m^2 + 3 m x' + 3 (x')^2 ] \cdot \\ & \{ l z' [ (x')^3 + \frac{l^3}{4} - (x' + m)^3 ] + l [ (x' + \\ & m)^3 - (x')^3 ] \}^{-1} \end{aligned} \tag{11} $$

由式(11)可以看出, 当区间 $[x', x' + m]$ 或 $[l - x' - m, l - x']$ 内的刚度与其他区间内的刚度不同时, 2 个点 ($x_1 = x' + m$ 和其相应的对称位置点) 的 DIILSR 值将与周围点不同。

由上可见, 虚拟分割和竖向支座反力影响线是形成这一新指标的两大要素。对于每个分割节点, 竖向支座反力的影响线数值被乘以 $F$, 然后对每对对称位置点取差值, 再取其相邻点的差值来得到 DIILSR 值。因此 DIILSR 的核心概念即为支座反力影响线二次差值。

## 2 损伤情况分析

### 2.1 损伤情形 1

损伤情形 1 如图 2 所示。

![Diagram of a two-span continuous beam with damage case 1, showing load F and segments d1, d2.](page_3_image_1_v2.jpg)

图 2 损伤情形 1
Fig. 2 Damage Case 1

图 2 中具有初始不同刚度的区间记为 $d_1$ (区间内的局部刚度记为 $z_1' E I$), 有损伤的区间记为 $d_2$ (区间内的局部刚度记为 $z_2' E I$), $z_1', z_2'$ 均为待定参数。这 2 个区间的位置关系是: $d_1$ 的对称位置与 $d_2$ 相邻。进行与上文类似的分析后可得

$$ \begin{aligned} \dot{\Delta} q_2^{x'} = & \dot{\Delta} q_2^{x'} - \bar{\Delta} q_2^{x'} = - F m x' [ 4 m^2 z_2' - 28 m^2 z_1' - \\ & 12 (x')^2 (z_1' - z_2') + 9 m l z_1' - 3 m l z_2' ] \cdot \\ & (12 l z_1' z_2' E I \delta_{11})^{-1} + \{ - F m x' [ - 36 m x' z_1' + \\ & 12 m x' z_2' + 6 l x' (z_1' - z_2') + 24 m^2 z_1' z_2' - \\ & 6 m l z_1' z_2' + 24 m x' z_1' z_2' ] \} (12 l z_1' z_2' E I \delta_{11})^{-1} \end{aligned} \tag{12} $$

$$ \begin{aligned} \dot{\Delta} q_2^{x' - m} = & F m (m - x') [ 4 m^2 z_2' - 28 m^2 z_1' - \\ & 12 (x')^2 (z_1' - z_2') + 9 m l z_1' - 3 m l z_2' ] \cdot \\ & (12 l z_1' z_2' E I \delta_{11})^{-1} + F m (m - x') \cdot \\ & [ - 36 m x' z_1' + 12 m x' z_2' + 6 l x' (z_1' - z_2') + \\ & 24 m^2 z_1' z_2' - 6 m l z_1' z_2' + 24 m x' z_1' z_2' ] \cdot \\ & (12 l z_1' z_2' E I \delta_{11})^{-1} \end{aligned} \tag{13} $$

$$ \begin{aligned} \dot{\Delta} q_2^{x' - 2m} = & F m (2 m - x') [ 4 m^2 z_2' - 28 m^2 z_1' - \\ & 12 (x')^2 (z_1' - z_2') + 9 m l z_1' - 3 m l z_2' ] \cdot \\ & (12 l z_1' z_2' E I \delta_{11})^{-1} + F m (2 m - x') \cdot \\ & [ - 36 m x' z_1' + 12 m x' z_2' + 6 l x' (z_1' - z_2') + \\ & 24 m^2 z_1' z_2' - 6 m l z_1' z_2' + 24 m x' z_1' z_2' ] \cdot \\ & (12 l z_1' z_2' E I \delta_{11})^{-1} \end{aligned} \tag{14} $$

$$ \begin{aligned} \dot{\Delta} q_2^{x' - 3m} = & F m (3 m - x') [ 4 m^2 z_2' - 28 m^2 z_1' - \\ & 12 (x')^2 (z_1' - z_2') + 9 m l z_1' - 3 m l z_2' ] \cdot \\ & (12 l z_1' z_2' E I \delta_{11})^{-1} + F m (3 m - x') \cdot \\ & [ - 36 m x' z_1' + 12 m x' z_2' + 6 l x' (z_1' - z_2') + \\ & 24 m^2 z_1' z_2' - 6 m l z_1' z_2' + 24 m x' z_1' z_2' ] \cdot \\ & (12 l z_1' z_2' E I \delta_{11})^{-1} \end{aligned} \tag{15} $$

进而可得

$$ \begin{aligned} \Delta_{x_1 = x'} = & \Delta_{x_1 = x' - m} = \Delta_{x_1 = x' - 2m} = - F m^2 [ 4 m^2 z_2' - \\ & 28 m^2 z_1' - 12 (x')^2 (z_1' - z_2') + 9 m l z_1' - 3 m l z_2' ] \cdot \end{aligned} $$

$$ (12 l z_{1}^{'} z_{2}^{'} E I \delta_{11})^{-1} + \{-F m^{2} [-36 m x^{'} z_{1}^{'} + 12 m x^{'} z_{2}^{'} + 6 l x^{'} (z_{1}^{'} - z_{2}^{'}) + 24 m^{2} z_{1}^{'} z_{2}^{'} - 6 m l z_{1}^{'} z_{2}^{'} + 24 m x^{'} z_{1}^{'} z_{2}^{'}] \} (12 l z_{1}^{'} z_{2}^{'} E I \delta_{11})^{-1} \quad (16) $$

当荷载 $F$ 出现在 $x_{1} = x^{'} + m$ 和其对称位置时, 可得

$$ \begin{aligned} \Delta_{x_{1} = x^{'} + m} &= \dot{\Delta} q_{2}^{x^{'} + m} - \dot{\Delta} q_{2}^{x^{'}} = F m^{2} [28 m^{2} z_{1}^{'} - 4 m^{2} z_{2}^{'} + 12 (x^{'})^{2} (z_{1}^{'} - z_{2}^{'}) - 9 m l z_{1}^{'} + 2 m l z_{2}^{'} + 36 m x^{'} z_{1}^{'}] (12 l z_{1}^{'} z_{2}^{'} E I \delta_{11})^{-1} + \\ & F m^{2} (-12 m x^{'} z_{2}^{'} - 6 l x^{'} z_{1}^{'} + 3 l x^{'} z_{2}^{'} - 24 m^{2} z_{1}^{'} z_{2}^{'} + 7 m l z_{1}^{'} z_{2}^{'} - 24 m x^{'} z_{1}^{'} z_{2}^{'} + 3 l x^{'} z_{1}^{'} z_{2}^{'}) (12 l z_{1}^{'} z_{2}^{'} E I \delta_{11})^{-1} \end{aligned} \quad (17) $$

对比式 (16), (17) 可得
$$ \begin{aligned} \Delta_{x_{1} = x^{'} + m} - \Delta_{x_{1} = x^{'}} &= [F m^{2} (m + 3 x^{'}) (z_{1}^{'} - 1)] \cdot (12 z_{1}^{'})^{-1} \{ \frac{l^{3}}{48} - \frac{2 m^{2} x^{'} + m x (x^{'})^{2}}{2} - \frac{2 m^{3}}{3} + \\ & [m^{3} + 3 m^{2} x^{'} + 3 m x (x^{'})^{2}] (12 z_{1}^{'})^{-1} + [7 m^{3} + 9 m^{2} x^{'} + 3 m (x^{'})^{2}] (12 z_{1}^{'})^{-1} \}^{-1} \end{aligned} \quad (18) $$

当 $F$ 出现在 $x_{1} = x^{'} + 2 m$ 和其对称位置时, 有
$$ \begin{aligned} \Delta_{x_{1} = x^{'} + 2 m} &= -F m^{2} [4 m^{2} z_{2}^{'} - 28 m^{2} z_{1}^{'} - 12 (x^{'})^{2} (z_{1}^{'} - z_{2}^{'}) + 5 m l z_{1}^{'} (1 - z_{2}^{'})] (12 l z_{1}^{'} z_{2}^{'} E I \delta_{11})^{-1} + \\ & \{-F m^{2} [-36 m x^{'} z_{1}^{'} + 12 m x^{'} z_{2}^{'} + 3 l x^{'} z_{1}^{'} (1 - z_{2}^{'}) + 24 m z_{1}^{'} z_{2}^{'} (m + x^{'})] \} \cdot (12 l z_{1}^{'} z_{2}^{'} E I \delta_{11})^{-1} \end{aligned} \quad (19) $$

对比式 (17), (19) 可得
$$ \Delta_{x_{1} = x^{'} + 2 m} - \Delta_{x_{1} = x^{'} + m} = -F m^{2} (2 m z_{2}^{'} - 4 m z_{1}^{'} - 3 x^{'} z_{1}^{'} + 3 x^{'} z_{2}^{'} + 2 m z_{1}^{'} z_{2}^{'}) (12 l z_{1}^{'} z_{2}^{'} E I \delta_{11})^{-1} \quad (20) $$

当荷载 $F$ 出现在 $x_{1} = x^{'} + 3 m, x_{1} = x^{'} + 4 m$ 及其对称位置时, 可类似得到 $\dot{\Delta} q_{2}^{x^{'} + 3 m}$ 和 $\dot{\Delta} q_{2}^{x^{'} + 4 m}$。综上, 可发现以下规律
$$ \Delta_{x_{1} = x^{'} + 3 m} = \Delta_{x_{1} = x^{'} + 4 m} = -F m^{2} [m^{2} z_{2}^{'} - 7 m^{2} z_{1}^{'} - 3 (x^{'})^{2} (z_{1}^{'} - z_{2}^{'}) - 9 m x^{'} z_{1}^{'} + 3 m x^{'} z_{2}^{'} + 6 m z_{1}^{'} z_{2}^{'} (m + x^{'})] (3 l z_{1}^{'} z_{2}^{'} E I \delta_{11})^{-1} \quad (21) $$

由式 (21) 可见, 损伤情形 1 下, 2 组对称点 ($x_{1} = x^{'} + m, x_{1} = l - x^{'} - m$ 和 $x_{1} = x^{'} + 2 m, x_{1} = l - x^{'} - 2 m$) 的 DIILSR 值与其他点的 DIILSR 值不同。

### 2.2 损伤情形 2


损伤情形 2 如图 3 所示。图 3 中具有初始不同刚度的区间 $d_{1}$ (区间内的局部刚度也记为 $z_{1}^{'} E I$) 与损伤的区间 $d_{2}$ (区间内的局部刚度也记为 $z_{2}^{'} E I$) 的位置关系是互相对称。进行类似的分析后可得: 损伤情形 2 下, 1 组对称点 ($x_{1} = x^{'} + m, x_{1} = l - x^{'} - m$) 的 DIILSR 值将与其他点的 DIILSR 值不同。

![Diagram of a beam with supports and loads, showing damage cases.](page_4_chart_1_v2.jpg)

图 3 损伤情形 2

Fig. 3 Damage Case 2

根据上述规律即可建立起基于 DIILSR 的损伤定位方法。

## 3 基于 DIILSR 的损伤定位方法

为便于描述, 首先将主梁的纵向设定为 $x$ 轴方向, 左端点视为原点 $O$, 某研究截面到原点的距离记为 $x_{2}$。在实际应用时, 应得到每个虚拟分割节点处的 DIILSR 值并作图。为考虑初始不确定性, 应对桥梁损伤前、后分别进行 1 次测试。然后考察每个点与相邻两点连线的斜率情况。

### 3.1 桥梁状态完好的情况

此时区间 $[x^{'}, x^{'} + m]$ 内具有与其他区间不同的局部刚度。虚拟分割节点处的 DIILSR 值从 $A \cdot (B - 3 l m)$ (即 $x_{2} < x^{'} + m$ 时) 到 $A (B - 2 l m)$ (即 $x_{2} = x^{'} + m$ 时), 再到 $A (B + 6 l x^{'})$ (即 $x_{2} > x^{'} + m$ 时)。其中

$$ A = [F m^{2} (z^{'} - 1) 12 z^{'} ] [12 (x^{'})^{3} l (z^{'})^{2} + 3 l^{4} (z^{'})^{2} - 12 (x^{'} + m)^{3} l (z^{'})^{2} + (x^{'} + m)^{3} - (x^{'})^{3}]^{-1} $$
$$ B = 4 m^{2} + 12 m x^{'} + 12 (x^{'})^{2} - 6 l x^{'} $$

由上式可知: 当 $x_{2} < x^{'}$ 时, 每个点与相邻点连线的斜率都为 0; 当 $x_{2} = x^{'}$ 时, 该点与左侧临近点的连线斜率为 0, 而与右侧临近点的连线斜率为 $C \cdot (m + 3 x^{'})$; 当 $x_{2} = x^{'} + m$ 时, 该点与左侧临近点的连线斜率为 $C (m + 3 x^{'})$, 而与右侧临近点的连线斜率为 $C (2 m + 3 x^{'})$; 当 $x_{2} = x^{'} + 2 m$ 时, 该点与左侧临近点的连线斜率为 $C (2 m + 3 x^{'})$, 而与右侧临近点的连线斜率为 0; 当 $x_{2} > x^{'} + 2 m$, 每个点与相邻点连线的斜率又变为 0。其中

$$ C = 4 F m (z^{'} - 1) [4 (x^{'})^{3} z^{'} + l^{3} z^{'} - 4 (x^{'} + m)^{3} z^{'} + 4 (x^{'} + m)^{3} - 4 (x^{'})^{3}]^{-1} $$

可见, 只有点 $x_{2} = x^{'} + m$ 与相邻两点连线的斜率都不为 0。该点可称为“特殊点”, 其指示出具有初始不同刚度的区间是 $[x^{'}, x^{'} + m]$ 或 $[l - x^{'} - m, l - x^{'}] $。

### 3.2 出现损伤情形 1 的情况

当 $x_{2} < x^{'} + m$ 时, 各节点处的 DIILSR 值为 $D (E - 3 m l z_{2}^{'} - 6 l x^{'} z_{2}^{'} - 6 m l z_{1}^{'} z_{2}^{'})$; 当 $x_{2} = x^{'} + m$ 时, 节点处的 DIILSR 值为 $-D (E - 2 m l z^{'} - \dots$

$3lx^{'}z_{2}^{'}-7mlz_{1}^{'}z_{2}^{'}-3lx^{'}z_{1}^{'}z_{2}^{'})$; 当 $x_{2}=x^{'}+2m$ 时, 节点处的 DIILSR 值为 $D(E-4mlz_{2}^{'}-5mlz_{1}^{'}z_{2}^{'}-3lx^{'} \cdot z_{1}^{'}-3lx^{'}z_{1}^{'}z_{2}^{'})$; 当 $x_{2}>x^{'}+2m$ 时, 各节点处的 DIILSR 值为 $D(E-9mlz_{1}^{'}-6lx^{'}z_{1}^{'})$。其中

$$
\begin{aligned}
D = & -4Fm^{2}[l^{4}z_{1}^{'}z_{2}^{'}-48m^{2}x^{'}lz_{1}^{'}z_{2}^{'} + \\
& 24mx^{'2}lz_{1}^{'}z_{2}^{'}-32m^{3}lz_{1}^{'}z_{2}^{'}+4m^{3}lz_{2}^{'} + \\
& 12m^{2}x^{'}lz_{2}^{'}+12m(x^{'})^{2}lz_{2}^{'}+28m^{3}lz_{1}^{'} + \\
& 36m^{2}x^{'}lz_{1}^{'}+12m(x^{'})^{2}lz_{1}^{'}]^{-1}
\end{aligned}
$$

$$
\begin{aligned}
E = & 4m^{2}z_{2}^{'}-28m^{2}z_{1}^{'}-12(x^{'})^{2}(z_{1}^{'}-z_{2}^{'}) + \\
& 9mlz_{1}^{'}-36mx^{'}z_{1}^{'}+12mx^{'}z_{2}^{'} + \\
& 6lx^{'}z_{1}^{'}+24m^{2}z_{1}^{'}z_{2}^{'}+24mx^{'}z_{1}^{'}z_{2}^{'}
\end{aligned}
$$

可见: 当 $x_{2}<x^{'}$ 时, 每个点与相邻点连线的斜率都为 0; 当 $x_{2}=x^{'}$ 时, 该点与左侧临近点的连线斜率为 0, 而与右侧临近点的连线斜率为 $H(m+3x^{'}) \cdot (z_{1}^{'}-1)$; 当 $x_{2}=x^{'}+m$ 时, 该点与左侧临近点的连线斜率为 $H(m+3x^{'})(z_{1}^{'}-1)$, 而与右侧临近点的连线斜率为 $-H(2mz_{2}^{'}-4mz_{1}^{'}-3x^{'}z_{1}^{'}+3x^{'}z_{2}^{'}+2mz_{1}^{'}z_{2}^{'})(z_{2}^{'})^{-1}$; 当 $x_{2}=x^{'}+2m$ 时, 该点与左侧临近点的连线斜率为 $-H(2mz_{2}^{'}-4mz_{1}^{'}-3x^{'}z_{1}^{'}+3x^{'}z_{2}^{'}+2mz_{1}^{'}z_{2}^{'})(z_{2}^{'})^{-1}$, 而与右侧临近点的连线斜率为 $H(5m+3x^{'})z_{1}^{'}(1-z_{2}^{'})(z_{2}^{'})^{-1}$; 当 $x_{2}=x^{'}+3m$ 时, 该点与左侧临近点的连线斜率为 $H(5m+3x^{'})z_{1}^{'} \cdot (1-z_{2}^{'})(z_{2}^{'})^{-1}$, 而与右侧临近点的连线斜率为 0; 当 $x_{2}>x^{'}+3m$ 时, 每个点与相邻点连线的斜率又变为 0。这样, 出现了两点与其相邻两点连线的斜率都不为 0, 新增加的“特殊点”($x_{2}=x^{'}+2m$)能指示出损伤出现的区间。其中

$$
\begin{aligned}
H = & 4Fmz_{2}^{'}[l^{3}z_{1}^{'}z_{2}^{'}-48m^{2}x^{'}z_{1}^{'}z_{2}^{'} - \\
& 24m(x^{'})^{2}z_{1}^{'}z_{2}^{'}-32m^{3}z_{1}^{'}z_{2}^{'}+4m^{3}z_{2}^{'} + \\
& 12m^{2}x^{'}z_{2}^{'}+12m(x^{'})^{2}z_{2}^{'}+7m^{3}z_{1}^{'} + \\
& 9m^{2}x^{'}z_{1}^{'}+3m(x^{'})^{2}z_{1}^{'}]^{-1}
\end{aligned}
$$

### 3.3 出现损伤情形 2 的情况

当 $x_{2}<x^{'}+m$ 时, 各节点处的 DIILSR 值为 $J(B-3lm)$; 当 $x_{2}=x^{'}+m$ 时, 节点处的 DIILSR 值为 $J(B-2lm)$; 当 $x_{2}>x^{'}+m$ 时, 各节点处的 DIILSR 值为 $J(B+6lx^{'})$。其中

$$
\begin{aligned}
J = & 4Fm^{2}(z_{1}^{'}-z_{2}^{'})[l^{4}z_{1}^{'}z_{2}^{'}-24m^{2}x^{'}lz_{1}^{'}z_{2}^{'} - \\
& 24m(x^{'})^{2}lz_{1}^{'}z_{2}^{'}-8m^{3}lz_{1}^{'}z_{2}^{'}+4m^{3}lz_{2}^{'} + \\
& 12m^{2}x^{'}lz_{2}^{'}+12m(x^{'})^{2}lz_{2}^{'}+4m^{3}lz_{1}^{'} + \\
& 12m^{2}x^{'}lz_{1}^{'}+12m(x^{'})^{2}lz_{1}^{'}]^{-1}
\end{aligned}
$$

可见, 此时的“特殊点”与桥梁处于完好状态时相同。但可以发现: 当 $x_{2}=x^{'}$ 时, 该点与右侧临近点的连线斜率为 $K(m+3x^{'})$; 当 $x_{2}=x^{'}+m$ 时, 该

点与左侧临近点的连线斜率为 $K(m+3x^{'})$, 与右侧临近点的连线斜率为 $K(2m+3x^{'})$; 当 $x_{2}=x^{'}+2m$ 时, 该点与左侧临近点的连线斜率为 $K(2m+3x^{'})$。其中

$$
\begin{aligned}
K = & 4Fm(z_{1}^{'}-z_{2}^{'})[l^{3}z_{1}^{'}z_{2}^{'}-24m^{2}x^{'}z_{1}^{'}z_{2}^{'} - \\
& 24m(x^{'})^{2}z_{1}^{'}z_{2}^{'}-8m^{3}z_{1}^{'}z_{2}^{'}+4m^{3}z_{2}^{'} + \\
& 12m^{2}x^{'}z_{2}^{'}+12m(x^{'})^{2}z_{2}^{'}+4m^{3}z_{1}^{'} + \\
& 12m^{2}x^{'}z_{1}^{'}+12m(x^{'})^{2}z_{1}^{'}]^{-1}
\end{aligned}
$$

即, 可从连线斜率变化来区分出现的损伤情形。

总之, 通过对比损伤前后的 DIILSR 图形可以实现损伤定位, 如果某点($x_{2}=x^{'}+m$)与相邻两点连线的斜率在这 2 个图形上发生了变化, 则说明损伤出现在区间$[x^{'}, x^{'}+m]$或$[l-x^{'}-m, l-x^{'}]。$本结论可以推广到不止 1 个区间具有初始不同的抗弯刚度或发生损伤的情况。

## 4 实施步骤

具体实施步骤如下:

(1) 首先选取合适的 $m$ 值。$m$ 取值越小, 则对损伤的定位越精细, 但工作量越大、成本越高。因此 $m$ 最佳取值和具体的应用情形有关。可先对桥梁进行初步的检查, 再结合经济条件来确定 $m$ 取值。

(2) 对于刚竣工的桥梁, 让集中荷载 $F$ 依次作用在各个虚拟分割的节点处, 记录对应于每个加载位置的中间支座竖向反力增加值。

(3) 根据前述的方法对这些数据进行处理得到 DIILSR 值并作图。

(4) 当桥梁有损伤后, 进行一次同样的加载和数据记录过程, 然后处理得到新的 DIILSR 图形。

(5) 对比这 2 个图形, 可发现损伤出现的区间。

(6) 在小区间内采用检测仪器来发现具体的损伤位置和情形。

施加荷载时应注意: 为了避免主梁横向效应的不利影响, 荷载 $F$ 应沿主梁的中轴线施加; 根据式(18)可知, $F$ 值越大, 则损伤引起的 DIILSR 值突变幅度越大, 越容易被发现, 但 $F$ 值过大可能会造成结构的人为损伤, 所以应合理控制 $F$ 的取值, 以使主梁上各点的应力不超出弹性极限为原则选取较大的 $F$ 值; 如果不方便直接实现集中荷载的施加, 可采取变通形式, 即用 2 个不同的卡车分 2 次加载, 经处理后可等同于施加集中荷载。

2 次加载具体方式如下:

(1) 卡车 1 加载。将卡车 1 的前轮停在某虚拟分割节点处。卡车前后轴提供的荷载近似认为是集中荷载,分别记为 $F_1, F_2$ (图 4)。记录此时各支座处产生的竖向反力数据,然后将卡车 1 驶离。

![Sketch of Truck Loading](page_6_image_1_v2.jpg)

图 4 卡车加载示意
**Fig. 4 Sketch of Truck Loading**

(2)卡车 2 加载。将卡车 2 的前后轮停在与卡车 1 相同的位置,但卡车 2 的型号和卡车 1 不同。通过调节卡车 2 的前后轴重,使后轴提供的集中荷载值为 $F_1$,前轴提供的集中荷载 $F_3 (F_3 < F_2)$。记录此时各测点处的竖向反力数据。基于弹性理论,可将 2 次记录的竖向支座反力数据相减,得到的数据相当于在该虚拟分割节点处集中荷载 $(F_2 - F_3)$ 作用下的数据。

## 5 算例分析

某两跨连续梁桥的跨度为 $2 \times 50\text{ m}$,截面为箱形,高度、宽度和壁厚分别为 $3, 6, 0.4\text{ m}$。采用 ANSYS 建立了有限元模型, $E = 32\,500\text{ MPa}, m = 1\text{ m}, F = 100\text{ kN}$。考虑初始不确定性,假设各区间间的等效抗弯刚度 $z'EI$ 如表 1 所示,为简洁起见,只考虑了 2 个对称部分 $x_2 \in [6\text{ m}, 14\text{ m}] \cup [86\text{ m}, 94\text{ m}]$ 的不确定性。

表 1 各分割区间的 $z'EI$

**Tab. 1 $z'EI$ of Each Partitioned Interval**

| 区间 | z′EI | 区间 | z′EI |
|---|---|---|---|
| [6m，7m） | 0.99EI | [86m，87m） | 0.98EI |
| [7m，8m） | 0.98EI | [87m，88m） | 0.99EI |
| [8m，9m） | 1.01EI | [88m，89m） | 1.01EI |
| [9m，10m） | 0.98EI | [89m，90m） | 0.99EI |
| [10m，11m） | 0.99EI | [90m，91m） | 0.98EI |
| [11m，12m） | 1.02EI | [91m，92m） | 1.02EI |
| [12m，13m） | 0.99EI | [92m，93m） | 0.99EI |
| [13m，14m] | 0.98EI | [93m，94m] | 0.98EI |

损伤情形假定为: $x = 90\text{ m}, y = 0.1\text{ m}$,梁截面的等效高度降低为 $2.9\text{ m}$。损伤前后的 DIILSR 值如图 5 所示。

由图 5 可见,点 $x_2 = 10\text{ m}$ 与相邻两点连线的斜率发生了变化,意味着损伤出现在区间 $[9\text{ m}, 10\text{ m}]$ 或 $[90\text{ m}, 91\text{ m}]$,与设定的损伤情形一致,验证了本文方法的有效性。

图 5 损伤前后的数据
**Fig. 5 Data Under Intact State and Damaged State**

## 6 结语

(1)本文方法只需对中间支座的竖向反力增量进行测量。目前已有商用的特种支座(装备了应变传感单元)可直接提供竖向支座反力数据。实桥的尺寸越大、限于条件可承受的测点数越少,则本文方法的优势越突出。

(2)本文方法数据处理过程简单,计算量小。

(3)采用区间作为几何度量单位,便于分析和计算。虽然定位的直接结果是 1 对小区间,但结合检测设备可方便地确定损伤情形。

(4)损伤定位的效果可通过改变荷载 $F$ 和虚拟区间长度 $m$ 的取值来进行调节。$F$ 值越大、$m$ 值越小,则效果越好。

(5)本文的分析是在一维框架内进行的,后续研究可将分析扩展到二维情形,从而充分考虑桥梁的横向效应。另外,可将 DIILSR 指标向损伤程度的判定方面进行扩展性研究。

参考文献:

**References:**

[1] MORASSI A. Damage Detection and Generalized Fourier Coefficients[J]. Journal of Sound and Vibration, 2007, 302(1): 229-259.

[2] HUTH O, FELTRIN G, MAECK J, et al. Damage Identification Using Modal Data: Experiences on a Prestressed Concrete Bridge[J]. Journal of Structural Engineering, 2005, 131(12): 1898-1919.

[3] CURADELLI R O, RIERA J D, AMBROSINI D, et al. Damage Detection by Means of Structural Damping Identification [J]. Engineering Structures, 2008, 30(12): 3497-3504.

[4] REYNDERS E, ROECK G D, BAKIR P G, et al. Damage Identification on the Tilff Bridge by Vibration Monitoring Using Optical Fiber Strain Sensors [J]. Journal of Engineering Mechanics, 2007, 133(2):185-193.

[5] LI Y Y, CHENG L, YAM L H, et al. Identification of Damage Locations for Plate-like Structures Using Damage Sensitive Indices: Strain Modal Approach [J]. Computers and Structures, 2002, 80(25): 1881-1894.

[6] LIU X, IEVEN N A J, ESCAMILLA-AMBROSIO P G. Frequency Response Function Shape-based Methods for Structural Damage Localization [J]. Mechanical Systems and Signal Processing, 2009, 23(4):1243-1259.

[7] TRENTADUE B, MESSINA A, GIANNOCCARO N I. Detecting Damage Through the Processing of Dynamic Shapes Measured by a PSD-triangular Laser Sensor [J]. International Journal of Solids and Structures, 2007, 44(17):5554-5575.

[8] ALVANDI A, CREMONA C. Assessment of Vibration-based Damage Identification Techniques [J]. Journal of Sound and Vibration, 2006, 292(1): 179-202.

[9] KIM J, PARK J, LEE B. Vibration-based Damage Monitoring in Model Plate-girder Bridges Under Uncertain Temperature Conditions [J]. Engineering Structures, 2007, 29(7):1354-1365.

[10] 冯 新,周 晶,陈健云,等. 基于观测柔度阵的结构参数识别[J]. 世界地震工程, 2002, 18(1):51-55.
FENG Xin, ZHOU Jing, CHEN Jian-yun, et al. Identification of Structural Parameters Based on Measured Flexibility [J]. World Earthquake Engineering, 2002, 18(1):51-55.

[11] YEO I, SHIN S, LEE H S, et al. Statistical Damage Assessment of Framed Structures from Static Responses [J]. Journal of Engineering Mechanics, 2000, 126(4):414-420.

[12] JANG J H, YEO I, SHIN S, et al. Experimental Investigation of System-identification-based Damage Assessment on Structures [J]. Journal of Structural Engineering, 2002, 128(5):673-682.

[13] PERERA R, HUERTA C, ORQUIN J M. Identification of Damage in RC Beams Using Indexes Based on Local Modal Stiffness [J]. Construction and Building Materials, 2008, 22(8):1656-1667.

[14] ZHANG Q W. Statistical Damage Identification for Bridges Using Ambient Vibration Data [J]. Computers and Structures, 2007, 85(7):476-485.

[15] CHANDRASHEKHAR M, GANGULI R. Damage Assessment of Structures with Uncertainty by Using Mode-shape Curvatures and Fuzzy Logic [J]. Journal of Sound and Vibration, 2009, 326(3/4/5):939-957.

[16] PAWAR P M, GANGULI R. Genetic Fuzzy System for Damage Detection in Beams and Helicopter Rotor Blades [J]. Computer Methods in Applied Mechanics and Engineering, 2003, 192(16/17/18):2031-2057.
