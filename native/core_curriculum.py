"""Original short explanations for a transparent, bounded starter curriculum.

These are bundled authored lessons, not claims that a language model was trained
or that the referenced websites were freshly fetched. Remote structured sources
supplement them with validated records. No executable exercises are included.
"""
VERSION = "2026.10.31.1"
REFERENCES = {
    "si": "https://www.bipm.org/en/measurement-units",
    "solar": "https://science.nasa.gov/solar-system/planets/",
    "json": "https://www.rfc-editor.org/rfc/rfc8259",
    "http": "https://www.rfc-editor.org/rfc/rfc9110",
    "unicode": "https://www.unicode.org/standard/WhatIsUnicode.html",
}
# id, domain, topic, aliases, original explanation, optional fixed reference id
LESSONS = (
    ("light-year", "astronomy", "光年是什么", ("光年", "light year"), "光年是距离单位，不是时间单位。它表示光在真空中一个儒略年传播的距离，约为 9.46 万亿千米。", "si"),
    ("astronomical-unit", "astronomy", "天文单位是什么", ("天文单位", "astronomical unit"), "天文单位（au）是长度单位，定义为 149,597,870,700 米，常用于描述太阳系内的距离。地球到太阳的实际距离会随轨道位置变化。", "si"),
    ("planets", "astronomy", "太阳系的八大行星", ("八大行星", "太阳系行星", "行星顺序"), "按离太阳由近到远，八大行星是水星、金星、地球、火星、木星、土星、天王星、海王星。冥王星属于矮行星。", "solar"),
    ("moon-light", "astronomy", "月亮为什么会亮", ("月亮为什么亮", "月光", "月亮发光"), "月亮通常可见的亮光主要来自反射的太阳光，并不是像太阳那样靠内部核聚变发光。", "solar"),
    ("moon-phases", "astronomy", "月相的原因", ("月相", "月亮圆缺"), "月相主要取决于太阳、地球和月球的相对位置，以及我们从地球上看到的月球受光部分。普通月相变化不是地球影子遮住月亮。", "solar"),
    ("seasons", "science", "四季为什么变化", ("四季", "季节变化", "冬天夏天"), "地球自转轴相对公转轨道面的倾斜，使不同季节的日照角度和白昼长度变化。四季不是简单由地球离太阳远近造成的。", "solar"),
    ("metre", "science", "米与千米", ("千米", "公里", "一米"), "米是国际单位制的长度基本单位。1 千米等于 1,000 米，“公里”通常就是千米。", "si"),
    ("second", "science", "秒与时间单位", ("秒是什么", "分钟", "一小时"), "秒是国际单位制的时间基本单位。通常的单位换算里，1 分钟是 60 秒，1 小时是 60 分钟。历法和时区规则是另一回事。", "si"),
    ("mass", "science", "质量和重量", ("质量和重量", "千克", "公斤"), "千克是质量单位；日常所说的“公斤”通常指千克。物理学中的重量是一种力，会受重力环境影响，不能把二者处处当作同一物理量。", "si"),
    ("power-energy", "science", "功率与能量", ("功率", "能量", "瓦特", "焦耳"), "能量描述可转移或转化的量，常用焦耳表示；功率描述能量变化的快慢，1 瓦特等于每秒 1 焦耳。", "si"),
    ("temperature", "science", "开尔文和摄氏度", ("开尔文", "摄氏度", "温度换算"), "开尔文是国际单位制的热力学温度单位。摄氏温度与开尔文温度的数值关系为 t/°C = T/K − 273.15；温差 1 K 与温差 1 °C 的大小相同。", "si"),
    ("speed", "science", "速度是什么", ("速度是什么", "平均速度", "路程时间"), "在简单的匀速或平均速率问题中，速率可以用路程除以用时计算。严格的速度还包含方向，不应把所有运动都简化成一条直线。", "si"),
    ("water-formula", "science", "水的化学式", ("水的化学式", "h2o", "水分子"), "水的化学式是 H₂O。一个水分子包含两个氢原子和一个氧原子；化学式不是制备或混合物质的操作指令。", ""),
    ("atom-molecule", "science", "原子和分子", ("原子和分子", "分子是什么", "原子是什么"), "原子是化学元素的基本结构单元。分子由原子结合形成；并非所有物质都由独立分子组成，例如金属和许多晶体需要用其他结构描述。", ""),
    ("bit-byte", "computing", "比特与字节", ("比特", "字节", "bit和byte", "bit与byte"), "比特（bit）表示一个二进制位；现代常见计算环境中，一个字节（byte）通常是 8 比特。b 与 B 的大小写在速率或容量标记中常用来区分它们。", ""),
    ("kib-kb", "computing", "KB与KiB", ("kib", "kb和kib", "kb与kib", "千字节"), "十进制前缀 k 表示 1,000；二进制前缀 Ki 表示 1,024。因此 1 kB 是 1,000 字节，1 KiB 是 1,024 字节。软件界面里的 KB 有时用法不一致，要看说明。", ""),
    ("unicode", "computing", "Unicode是什么", ("unicode", "统一码"), "Unicode 为字符提供统一的编码空间。字符、码点、字形和屏幕上看到的一个完整符号不总是一一对应，组合字符和表情序列尤其如此。", "unicode"),
    ("utf8", "computing", "UTF-8是什么", ("utf-8", "utf8"), "UTF-8 是 Unicode 的一种变长编码方式，通常用 1 到 4 个字节编码一个 Unicode 标量值。一个显示出来的表情可能由多个码点组成。", "unicode"),
    ("json", "computing", "JSON是什么", ("json", ".json"), "JSON 是用于交换结构化数据的文本格式，可表示对象、数组、字符串、数值、布尔值和 null。安全地解析数据不等于执行其中的字符串。", "json"),
    ("http", "computing", "HTTP与HTTPS", ("http", "https"), "HTTP 是应用层协议；HTTPS 通常是在 TLS 保护下传输 HTTP，能提供传输加密和服务器身份验证。它不自动保证网页内容真实或无害。", "http"),
    ("mime", "computing", "MIME类型是什么", ("mime", "内容类型"), "MIME 内容类型用类似 image/png 或 application/json 的标识说明内容格式。文件扩展名、服务器声明的类型和实际内容可能不一致，不能单靠其中一个判断安全。", "http"),
    ("lossless", "computing", "无损与有损压缩", ("无损压缩", "有损压缩"), "无损压缩允许完整恢复原始数据；有损压缩会舍弃部分信息以换取更小体积。是否合适取决于数据用途，而不只是文件大小。", ""),
    ("backup-sync", "computing", "备份与同步", ("备份", "同步"), "同步侧重让多处数据保持一致，删除或错误也可能被同步；备份侧重保留可恢复的历史副本。同步并不天然等于可靠备份。", ""),
    ("training-index", "computing", "训练与知识索引的区别", ("训练和索引", "训练与索引", "知识索引", "权重训练"), "权重训练会按某种学习规则改变模型参数；知识索引是把资料整理成可检索记录。下载、索引或检索成功，并不等于训练了大语言模型，也不证明理解能力提高。", ""),
    ("train-eval", "computing", "训练损失与测试效果", ("训练损失", "测试准确率", "过拟合", "拟合损失"), "同一个训练样本上的损失降低，只能说明该目标的拟合发生变化。要判断泛化效果，需要独立、合适的测试数据，不能把训练损失或分类置信度当作整体准确率。", ""),
    ("prime", "general", "质数是什么", ("质数", "素数"), "质数是大于 1、且只有 1 和自身两个正因数的整数。1 不是质数；2 是唯一的偶质数。", ""),
    ("mean-median", "general", "平均数与中位数", ("平均数", "中位数"), "算术平均数是所有数的和除以个数；中位数是排序后居中的位置值。极端值对平均数的影响通常更明显，两者各有用途。", ""),
    ("probability", "general", "概率不是确定承诺", ("概率是什么", "概率", "可能性"), "概率用于表达不确定性。某次结果发生或没发生，都不能单独证明一个概率预测正确或错误；还需要清楚的条件和足够的验证。", ""),
    ("latitude-longitude", "geography", "经纬度是什么", ("经纬度", "纬度", "经度"), "纬度表示位置相对赤道的南北角度，经度表示相对选定本初子午线的东西角度。坐标还依赖约定的参考系统，不能忽略坐标系差异。", ""),
    ("time-zone", "geography", "时区与时间差", ("时区", "时差", "utc"), "时区把当地民用时间与 UTC 等基准关联起来。不同地区可能采用不同偏移或夏令时规则，所以两地时差不一定全年固定。", ""),
)


def records():
    return [{"key": key, "domain": domain, "topic": topic, "aliases": list(aliases), "answer": answer, "intent": "concept", "reference": reference} for key, domain, topic, aliases, answer, reference in LESSONS]
