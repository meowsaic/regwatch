# -*- coding: utf-8 -*-
"""完善「风控合规部」版申报表（按评分标准对标调整）：
- 难易度：方案要点末尾补“（5）工程实现及技术难点”；
- 创新性：创新点前加岗位首创总起句；
- 应用效果：决策支撑处补专题报告具体数据（保留）；
- 简介：具体化四份专题报告名称（保留）；
- 经济效益：如实写“无”（未货币化），不编造数字；
- 推广情况：勾选“厂部内”（对应部门内推广）；
- 知识产权：不申请，保持空白（不填软件著作权）。
在原始文件基础上生成“-已完善”版本，不覆盖原件。
"""
import copy
from docx import Document
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.text.paragraph import Paragraph

SRC = r"E:\Desktop\codes\华宝投资优秀岗位创新成果奖申报-风控合规部-20260911.docx"
DST = r"E:\Desktop\codes\华宝投资优秀岗位创新成果奖申报-风控合规部-20260911-已完善.docx"

doc = Document(SRC)

def _first_rpr(para):
    for r in para.runs:
        rr = r._element.find(qn("w:rPr"))
        if rr is not None:
            return copy.deepcopy(rr)
    return None

def set_para_text(para, text, ref_para=None):
    ppr = para._p.find(qn("w:pPr"))
    rpr = _first_rpr(para)
    if rpr is None and ref_para is not None:
        rpr = _first_rpr(ref_para)
    for child in list(para._p):
        para._p.remove(child)
    if ppr is not None:
        para._p.append(ppr)
    r = OxmlElement("w:r")
    if rpr is not None:
        r.append(rpr)
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    r.append(t)
    para._p.append(r)

def insert_after(para, text):
    new_p = OxmlElement("w:p")
    ref_ppr = para._p.find(qn("w:pPr"))
    if ref_ppr is not None:
        new_p.append(copy.deepcopy(ref_ppr))
    rpr = _first_rpr(para)
    r = OxmlElement("w:r")
    if rpr is not None:
        r.append(rpr)
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    r.append(t)
    new_p.append(r)
    para._p.addnext(new_p)
    return Paragraph(new_p, para._parent)

def find_para(paras, contains):
    for p in paras:
        if contains in p.text:
            return p
    raise ValueError(f"未找到含“{contains}”的段落")

# ============================================================
# 1. 成果简介：具体化专题报告名称
# ============================================================
t1 = doc.tables[1].cell(0, 0)
jianjie2 = ("平台覆盖全市场数千条监管处分案例，提供网页看板与命令行双入口，支持按机构类型、违规类型、"
            "来源局、日期、关键词等多维度组合检索，可一键生成违规分布、处罚构成、机构与人员对比、法规引用、"
            "时间趋势等统计报告，并已在此基础之上形成《私募基金管理人非专业化运营边界分析报告》《AMAC内控不健全"
            "专项分析报告》《CSRC内控不健全专项分析报告》《私募基金管理人高级管理人员违规兼职典型案例分析》等"
            "专题研究成果。成果已应用于公司重大事项的前置合规评估，为经营管理决策提供专业数据支撑，促进科学决策与合规经营。")
set_para_text(t1.paragraphs[1], jianjie2, ref_para=t1.paragraphs[0])

# ============================================================
# 2. 详细内容
# ============================================================
t2 = doc.tables[2].cell(0, 0)
paras = t2.paragraphs

# 2.1 创新性总起（在“3.创新点”标题后）
p_chuangxin = find_para(paras, "3.创新点")
chuanxin_zong = "本成果在私募基金合规细分领域属岗位首创，填补了“监管处分案例”这一细分场景下全口径归集与智能分析的空白。"
insert_after(p_chuangxin, chuanxin_zong)

# 2.2 难易度（在方案要点第4层后）
p_fenxi = find_para(paras, "（4）分析展示层")
nandu = ("（5）工程实现及技术难点：本项目为个人在完成本职合规法务工作之余独立完成全链条开发，难度主要体现在——"
         "数据来源高度异构，需适配中国证券投资基金业协会及证监会 37 家派出机构 30 余个公告栏目，覆盖网页、PDF 与 OCR"
         "多种版式；数据体量约 1.6GB，需设计索引与按需读取机制以支持秒级检索；大模型结构化提取需针对监管文本进行提示词"
         "工程与两阶段扫描，控制幻觉并保证基金相关性判断精度；需构建跨两套监管体系的统一违规分类标准，兼顾可比性与专业"
         "准确性；同时完成断点续传、并发与幂等控制及 146 项自动化测试等工程质量保障。")
insert_after(p_fenxi, nandu)

# 2.3 应用效果（3）决策支撑处补专题成果详情
p_juice = find_para(paras, "（3）决策支撑")
detail = ("截至目前，平台已支撑形成四份专题研究成果：《私募基金管理人非专业化运营边界分析报告》基于"
          "2023年1月至2026年5月125份AMAC纪律处分案例，将“非专业化运营”归纳为兼营债券发行承销、财务顾问、"
          "借贷放贷、居间中介、定向融资、通道业务、代持理财等十余类典型行为并厘清认定逻辑；《AMAC内控不健全"
          "专项分析报告》基于2020年以来554个机构处分案例，揭示内控缺失占比由2021年的20%升至2025年的24.5%"
          "及高发环节分布；《CSRC内控不健全专项分析报告》基于2022年以来2019个案例，显示内控缺失占比由2022年的"
          "16.0%升至2026年的27.6%，印证监管对内控要求的持续收紧；《私募基金管理人高级管理人员违规兼职典型案例"
          "分析》覆盖两体系典型案例，得出“违规兼职通常与内控缺失、登记信息失实等多项违规一并处罚”的判断，"
          "为兼职合规审查提供口径参考。")
insert_after(p_juice, detail)

# ============================================================
# 3. 经济效益：如实写“无”
# ============================================================
t3 = doc.tables[3].cell(0, 0)
p3 = t3.paragraphs
ref_body = t2.paragraphs[1]
set_para_text(p3[1], "以平台替代合规人员人工检索、阅读、归类、统计监管处分案例所节约的工作时间为测算口径，体现成果带来的效率型经济效益。", ref_para=ref_body)
set_para_text(p3[3], "平台将一次监管处分案例的全口径检索与统计由上线前的数小时人工查阅缩短至秒级，使合规人员从重复性检索整理工作中解放出来，将更多时间投入到违规定性与风险研判等专业判断环节；相关违规分布、处罚构成、法规引用与时间趋势等统计可一键生成，进一步降低合规分析的时间投入。", ref_para=ref_body)
set_para_text(p3[5], "本成果价值主要体现在合规效率与决策质量的提升，未按货币金额进行单独测算。", ref_para=ref_body)

# ============================================================
# 4. 推广情况：勾选“厂部内”（对应部门内推广）
# ============================================================
tg_cell = doc.tables[0].rows[16].cells[1]
for p in tg_cell.paragraphs:
    for r in p.runs:
        if "厂部内" in r.text:
            r.text = r.text.replace("□", "√", 1)

doc.save(DST)
print("已生成:", DST)