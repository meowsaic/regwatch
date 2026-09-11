# -*- coding: utf-8 -*-
"""华宝投资优秀岗位创新成果奖申报表 —— 自动填充脚本
在原官方模板基础上，填充可确定字段与成果简介/详细内容/经济效益正文，
个人信息与单位信息以【待填】占位符标注，保留官方表格与分节格式。
"""
import copy
from docx import Document
from docx.shared import Pt
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.text.paragraph import Paragraph
from docx.enum.text import WD_ALIGN_PARAGRAPH

SRC = r"E:\Desktop\codes\华宝投资优秀岗位创新成果奖申报表-谭昊然-20260911.docx"
DST = r"E:\Desktop\codes\华宝投资优秀岗位创新成果奖申报表-谭昊然-20260911-已填写.docx"

W_R = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}r"
WP_DOCPR = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}docPr"

doc = Document(SRC)

# ============================================================
# 0. 删除提示用浮动文本框（文本框 23 / 24），正文将用普通段落重写
# ============================================================
def remove_textboxes(doc, names):
    body = doc.element.body
    removed = 0
    for el in list(body.iter(WP_DOCPR)):
        name = el.get("name") or ""
        if name.strip() in names:
            node = el
            while node is not None and node.tag != W_R:
                node = node.getparent()
            if node is not None:
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
                    removed += 1
    return removed

print("删除文本框:", remove_textboxes(doc, {"文本框 23", "文本框 24"}))

# ============================================================
# 1. 工具函数
# ============================================================
def find_para(text_contains, start=0):
    for i, p in enumerate(doc.paragraphs):
        if i >= start and text_contains in p.text:
            return p
    raise ValueError(f"未找到段落: {text_contains}")

def _set_run_font(run, size_pt=12, bold=False, east="宋体", ascii_f="Times New Roman"):
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    run.font.name = ascii_f
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), ascii_f)
    rfonts.set(qn("w:hAnsi"), ascii_f)
    rfonts.set(qn("w:eastAsia"), east)

def insert_para_after(anchor_el, text, size_pt=12, bold=False, east="宋体",
                      ascii_f="Times New Roman", align=None, first_indent_pt=None,
                      before_pt=0, after_pt=0, line_pt=None):
    p = OxmlElement("w:p")
    anchor_el.addnext(p)
    para = Paragraph(p, anchor_el.getparent())
    pf = para.paragraph_format
    if align is not None:
        pf.alignment = align
    if first_indent_pt is not None:
        pf.first_line_indent = Pt(first_indent_pt)
    pf.space_before = Pt(before_pt)
    pf.space_after = Pt(after_pt)
    if line_pt is not None:
        pf.line_spacing = line_pt
    if text != "":
        run = para.add_run(text)
        _set_run_font(run, size_pt=size_pt, bold=bold, east=east, ascii_f=ascii_f)
    return p

def set_cell(cell, text, size_pt=12, bold=False):
    """清空单元格全部段落内容并写入新文本（支持多行）。"""
    for p in cell.paragraphs[1:]:
        p._element.getparent().remove(p._element)
    first = cell.paragraphs[0]
    for r in list(first.runs):
        r._element.getparent().remove(r._element)
    for i, line in enumerate(text.split("\n")):
        run = first.add_run(line)
        _set_run_font(run, size_pt=size_pt, bold=bold)
        if i < len(text.split("\n")) - 1:
            run.add_break()

def append_cell(cell, text, size_pt=12, bold=False):
    """在单元格现有文本后追加内容。"""
    first = cell.paragraphs[0]
    run = first.add_run(text)
    _set_run_font(run, size_pt=size_pt, bold=bold)

# ============================================================
# 2. 基本情况表（Table 0）字段填充
# ============================================================
t0 = doc.tables[0]

成果名称 = "私募基金监管案例全口径智能采集与合规分析平台"
推荐单位_占位 = "【待填：总部部门或三级公司名称】"
责任单位_占位 = "【待填：项目责任单位（总部部门或三级公司）】"

set_cell(t0.rows[0].cells[1], 推荐单位_占位)                      # 推荐单位
set_cell(t0.rows[1].cells[1], 成果名称)                           # 成果名称

# 负责人信息行（R3）
set_cell(t0.rows[3].cells[3], "谭昊然")                           # 姓名
set_cell(t0.rows[3].cells[4], "【待填：工号】")                    # 工号
set_cell(t0.rows[3].cells[5], "技术业务类")                       # 岗位类型
set_cell(t0.rows[3].cells[7], "【待填：手机】")                    # 手机

append_cell(t0.rows[4].cells[1], "无（本项目为个人岗位创新成果）")   # 主要完成人
append_cell(t0.rows[5].cells[1], "无")                             # 协作者

set_cell(t0.rows[6].cells[1], 责任单位_占位)                       # 项目责任单位
set_cell(t0.rows[7].cells[1], "业务管理")                          # 专业领域

# 立项 / 应用时间（占位）
set_cell(t0.rows[9].cells[1], "【待填：年 月】")                    # 立项时间
set_cell(t0.rows[9].cells[6], "【待填：年 月】")                    # 应用时间

# 知识产权情况（专利 / 其他）
set_cell(t0.rows[10].cells[4], "发明专利：无\n实用新型专利：无\n外观设计专利：无")
set_cell(t0.rows[11].cells[4], "软件著作权（平台软件，详见《知识产权情况汇总表》）")

# ============================================================
# 3. 成果简介、详细内容、经济效益正文
# ============================================================
para_简介 = find_para("二、成果简介")
para_详细 = find_para("三、详细内容")
para_效益 = find_para("四、经济效益")

简介 = [
    "本成果面向私募基金监管全面从严背景下公司合规法务工作的现实痛点，自主设计并开发了“私募基金监管案例全口径智能采集与合规分析平台”。平台自动采集中国证券投资基金业协会（AMAC）纪律处分及中国证监会及其 37 家派出机构的行政处罚、监管措施案例，运用人工智能大模型对海量非结构化监管文本进行结构化提取，自动识别违规类型、处罚措施、涉及基金、法规依据、罚款金额、市场禁入等关键要素，并构建统一的违规分类体系与统计看板，实现由“人工检索查阅”到“全口径自动归集、智能研判、一键分析”的转变。",
    "平台覆盖全市场数千条监管处分案例，提供网页看板与命令行双入口，支持按机构类型、违规类型、来源局、日期、关键词等多维度组合检索，可一键生成违规分布、处罚构成、机构与人员对比、法规引用、时间趋势等统计报告，并已沉淀“非专业化运营边界”“内控不健全”等多份专题分析报告。成果已应用于公司重大事项的前置合规评估，为经营管理决策提供专业数据支撑，促进科学决策与合规经营。",
]

详细 = [
    ("1. 立项背景", True, [
        "近年来，监管部门对私募基金“扶优限劣、全面从严”的监管导向日益明确，中国证监会及派出机构、中国证券投资基金业协会每年发布的行政监管措施、行政处罚和纪律处分数量持续增长。相关案例文本分散于三十余个官方网站公告栏目，格式不一、多为 PDF 或网页，且以非结构化长文本为主。",
        "传统上，合规法务人员依靠人工逐条检索、阅读并归类监管处分案例，存在三方面突出问题：一是来源分散、覆盖面不全，派出机构公告分布零散，人工难以做到“全口径”归集，容易遗漏关键案例；二是效率低、口径不一，违规类型判断依赖个人经验，缺乏统一分类标准，跨人员、跨时期的结果不可比；三是难以支撑决策，分散的案例无法快速形成趋势研判与专题分析，难以在公司重大事项前置合规评估中及时提供量化、可追溯的依据。",
        "更深层次看，监管规则在不少领域存在中间地带与模糊地带，成文规定难以穷尽实践中的各种具体情形，监管的边界、执法态度与认定口径，往往需要透过大量真实案例才能逐步探明。同一类行为在不同时期、不同监管主体的处理可能呈现差异化的定性与尺度，唯有系统归集并横向、纵向比对历史案例，才能在事前合规评估中准确预判风险、把握监管口径。因此，监管处分案例既是“活的规则”，也是合规判断不可或缺的事实基础。",
        "为此，本人立足法务岗位职责，自主设计并开发了本平台，目标是实现监管处分案例的全口径采集、结构化提取与智能分析，把分散的“合规数据”转化为可复用的“决策依据”。",
    ]),
    ("2. 项目方案要点", True, [
        "总体思路：采用“自动采集 + 大模型结构化提取 + 统一分类体系 + 可视化分析”四位一体的技术路线，构建岗位级、低成本、可复用的一体化平台。",
        "（1）统一采集层：针对 AMAC 机构与人员纪律处分、CSRC 37 个来源的行政监管措施与行政处罚，开发专用采集器，支持日期范围与单链接抓取、断点续传与增量更新。",
        "（2）结构化提取层：接入统一 OpenAI 兼容大模型接口，通过两阶段扫描对案例正文进行结构化摘要提取，自动识别违规类型、处罚措施、涉及基金、法规依据及罚款金额、市场禁入等字段；对 CSRC 案例额外进行“基金相关性”精判，避免已知的“法规名含基金即误判”问题。",
        "（3）数据治理层：构建统一的违规分类体系（募集行为、投资运作、管理人义务、内部治理、信息披露与自律、操纵市场、内幕交易等），将两套独立数据集归一为统一视图，保证分析口径可比。",
        "（4）分析展示层：开发统计聚合与报告渲染引擎，配套五个网页页面（总览、案例浏览、统计分析、任务中心、模型与配置）与命令行双入口，支持 Markdown、HTML、JSON 多格式报告输出。",
    ]),
    ("3. 创新点", True, [
        "创新点一：全口径自动采集与断点续传。一次性覆盖 AMAC 及证监会 37 家派出机构全部处分案例来源，解决人工检索来源分散、易遗漏的问题，且支持断点续传与增量更新，保障数据完整性与可持续性。",
        "创新点二：基于大模型的监管文本结构化提取与基金相关性精判。将非结构化 PDF、网页文本自动转为结构化字段，相较传统关键词匹配显著降低误判；并针对 CSRC 标题粗筛易误判的行业共性难题，引入大模型精判环节，显著提升数据可信度。",
        "创新点三：统一违规分类体系与跨数据集归一视图。建立可比对的分类标准，实现 AMAC 自律处分与 CSRC 行政处罚两套体系在同一口径下横向、纵向与趋势对比，这是通用法规数据库（偏重法规检索）所不具备的细分能力。",
        "创新点四：岗位级、低成本、可复用的一体化平台。以统一 regwatch 包为核心，网页与命令行共用一套任务编排；数据按需读取支撑 1.6GB 数据秒级访问；配套 146 项纯本地单元测试与网页冒烟测试保障质量，便于长期维护与推广复用。",
    ]),
    ("4. 应用效果", True, [
        "（1）效率提升：实现全口径自动归集数千条监管处分案例，合规检索与统计由原来的“数小时人工查阅”压缩到“秒级”，显著释放法务人员用于价值判断与分析的时间。",
        "（2）质量提升：统一违规分类体系使违规定性口径一致、可追溯，为合规审查的稳定性和一致性提供依据。",
        "（3）决策支撑：已产出多份专题分析报告，应用于公司重大事项的前置合规评估，为经营管理决策提供专业数据参考，促进科学决策与合规经营。",
        "（4）可推广性：网页 + 命令行双入口、模块化设计，非技术人员亦可便捷使用，具备在部门内乃至跨部门、跨单位推广复用的基础。",
    ]),
]

效益 = [
    ("1. 计算依据", True, [
        "以平台替代法务人员人工“检索—阅读—归类—统计”监管处分案例的工作量节约为测算口径，将成果实施前后的合规分析工时差折算为经济效益。",
    ]),
    ("2. 计算方式", True, [
        "（1）单次作业节约：一次全口径监管案例检索与统计，人工约需 4 人·天，平台上线后约需 0.5 人·天（主要用于下载与核对），单次节约约 3.5 人·天。",
        "（2）年度作业频次：按年度开展重大事项前置合规评估、季度及专题监管分析合计约 20 次测算，年节约工时约 70 人·天。",
        "（3）年净增经济效益 = 年节约工时 × 法务人员日均人力成本（含薪酬、社保及管理费分摊）。按法务人员日均人力成本【待填：元/人·天】计算，年净增经济效益约为【待填：万元】。",
    ]),
    ("3. 成果实施后实得的年净增经济效益", True, [
        "年净增经济效益约【待填：万元】（以上为基础测算口径，请按实际工时与人力成本数据核算，并经推荐单位财务部门审核认定）。",
    ]),
]

# 插入简介正文（在“二、成果简介”标题之后）
anchor = para_简介._p
for seg in 简介:
    anchor = insert_para_after(anchor, seg, size_pt=12, first_indent_pt=24, after_pt=6, line_pt=1.5)

# 插入详细内容正文（在“三、详细内容”标题之后）
anchor = para_详细._p
for title, bold, paras in 详细:
    anchor = insert_para_after(anchor, title, size_pt=12, bold=bold, before_pt=6, after_pt=3)
    for seg in paras:
        anchor = insert_para_after(anchor, seg, size_pt=12, first_indent_pt=24, after_pt=3, line_pt=1.5)

# 经济效益：迁移“四、经济效益”段内的分节符到经济效益正文末尾，再在其后插入正文
def detach_sect_pr(para):
    ppr = para._p.find(qn("w:pPr"))
    if ppr is not None:
        sect = ppr.find(qn("w:sectPr"))
        if sect is not None:
            ppr.remove(sect)
            return sect
    return None

sect = detach_sect_pr(para_效益)

anchor = para_效益._p
for title, bold, paras in 效益:
    anchor = insert_para_after(anchor, title, size_pt=12, bold=bold, before_pt=6, after_pt=3)
    for seg in paras:
        anchor = insert_para_after(anchor, seg, size_pt=12, first_indent_pt=24, after_pt=3, line_pt=1.5)

# 将分节符挂回经济效益最后一个段落的 pPr（保持分页，承诺书另起新节）
if sect is not None:
    last_p = anchor
    ppr = last_p.find(qn("w:pPr"))
    if ppr is None:
        ppr = OxmlElement("w:pPr")
        last_p.insert(0, ppr)
    ppr.append(sect)

# ============================================================
# 4. 负责人信息表（Table 2）首行
# ============================================================
t2 = doc.tables[2]
r1 = t2.rows[1]
set_cell(r1.cells[0], "1")
set_cell(r1.cells[1], "谭昊然")
set_cell(r1.cells[2], "【待填：工号】")
set_cell(r1.cells[3], "【待填：单位及岗位，如 合规法务部 · 法务经理】")
set_cell(r1.cells[4], "项目负责人：需求设计、平台架构、采集与大模型提取方案、违规分类体系构建、成果应用与推广")

# ============================================================
# 5. 知识产权情况汇总表（Table 3）首行
# ============================================================
t3 = doc.tables[3]
r1 = t3.rows[1]
set_cell(r1.cells[0], "软件著作权")
set_cell(r1.cells[1], "私募基金监管案例全口径智能采集与合规分析平台（软件）")
set_cell(r1.cells[2], "【待填：登记号/受理号】")
set_cell(r1.cells[3], "【待填：认定时间】")
set_cell(r1.cells[4], "如尚未登记请注明“拟申请/受理中”")

doc.save(DST)
print("已生成:", DST)