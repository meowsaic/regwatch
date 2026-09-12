"""违规类型分类体系（AMAC / CSRC 单一权威来源）。

设计要点：

- **一套语义类型**：同一违规行为在两类文书中措辞可能不同
  （如 AMAC 写「未尽勤勉尽责义务」、CSRC 写「未勤勉尽责」），
  这里统一收敛到一个 canonical 名，避免跨数据集统计被拆散；
- **按数据集派生候选清单**：:func:`violation_candidates` 依据
  :attr:`ViolationType.datasets` 生成各提取提示词的候选类型，
  提示词不再各自维护一份列表；
- **别名归一**：模型常输出提示词里的子项表述（如「适当性管理不到位」
  「操纵证券价格」），由 :attr:`ViolationType.aliases` 映射回 canonical 类型。

**分类语义一经改动会影响历史报告可比性**：新增类型请追加到
:data:`_VIOLATION_DEFS` 末尾，并同步 ``AGENTS.md`` / ``README.md``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .enums import Dataset
from .models import split_multi_value

__all__ = [
    "PUNISHMENT_CATEGORIES",
    "UNCLASSIFIED",
    "VIOLATION_ADVICE",
    "VIOLATION_TYPES",
    "VIOLATION_TYPES_AMAC",
    "VIOLATION_TYPES_CSRC",
    "ViolationType",
    "advice_for",
    "canonical_violations",
    "categorize_punishment",
    "normalize_violations",
    "violation_candidates",
    "violation_label",
    "violations_text",
]

#: 无法归类时的占位类型
UNCLASSIFIED = "未分类"

#: 需要精确匹配（不做包含匹配）的短类型名，避免「其他情形」被误判为「其他」
_EXACT_MATCH_NAMES = frozenset({"其他"})

#: 片段内部的二级分隔符（「类型：子项」写法）
_SUB_SPLIT = re.compile(r"[：:]")


@dataclass(frozen=True, slots=True)
class ViolationType:
    """一个违规类型的完整定义。

    Attributes:
        name: canonical 统计键（写入 ``case_violations``、跨数据集合并统计）。
        aliases: 模型可能输出的子项 / 异名写法，归一化时映射回本类型。
        datasets: 适用数据集；提取提示词候选清单由它派生。
        labels: 按数据集的展示措辞（不参与统计，仅影响界面与报告文字）。
    """

    name: str
    aliases: tuple[str, ...] = ()
    datasets: tuple[Dataset, ...] = (Dataset.AMAC, Dataset.CSRC)
    labels: tuple[tuple[Dataset, str], ...] = ()

    def label_for(self, dataset: Dataset | str | None = None) -> str:
        """返回本类型在指定数据集下的展示名（无专名时回落到 canonical）。"""
        target = dataset if isinstance(dataset, Dataset) else Dataset.parse(dataset)
        if target is None:
            return self.name
        for candidate, label in self.labels:
            if candidate is target:
                return label
        return self.name


#: 全部类型的单一权威定义（顺序 = 统计展示顺序 = 归一匹配优先级）
_VIOLATION_DEFS: tuple[ViolationType, ...] = (
    ViolationType(
        "信息披露违规",
        aliases=(
            "虚假记载",
            "重大遗漏",
            "未按时披露定期报告",
            "未按期披露定期报告",
            "未按规定披露定期报告",
            "未披露重大事项",
            "未按约定披露基金信息",
            "未按规定披露基金信息",
            "虚假披露",
            "选择性披露",
            "监管信息报送失准",
            "信息披露虚假记载",
        ),
    ),
    ViolationType(
        "操纵市场",
        aliases=("操纵证券价格", "操纵证券市场", "操纵期货市场", "操纵证券交易价格", "操纵"),
        datasets=(Dataset.CSRC,),
    ),
    ViolationType(
        "内幕交易",
        aliases=("泄露内幕信息", "利用未公开信息交易", "内幕信息", "未公开信息"),
        datasets=(Dataset.CSRC,),
    ),
    ViolationType(
        "违规募集",
        aliases=(
            "向不合格投资者募集",
            "向不特定对象募集",
            "适当性管理",
            "承诺保本保收益",
            "承诺收益",
            "承诺最低收益",
            "违规外包销售",
            "宣传推介",
            "公开宣传",
            "保本保收益",
        ),
    ),
    ViolationType(
        "未按规定备案",
        aliases=(
            "基金产品未备案",
            "产品未备案",
            "未备案",
            "未及时更新备案信息",
            "未更新备案信息",
            "重大事项未报告",
            "未及时报告重大事项",
            "未按规定履行备案",
        ),
    ),
    ViolationType(
        "登记信息失实",
        aliases=(
            "虚假填报登记备案信息",
            "虚假填报",
            "高管人员信息不实",
            "人员信息不实",
            "未及时提交重大事项变更",
            "未及时变更重大事项",
            "登记备案信息不实",
        ),
    ),
    ViolationType(
        "违规投资运作",
        aliases=(
            "超范围投资",
            "超范围",
            "资金池",
            "加杠杆",
            "违反合同约定投资",
            "违反合同约定",
            "通道业务",
            "违规运用基金财产",
        ),
    ),
    ViolationType(
        "挪用基金财产",
        aliases=("侵占基金财产", "非约定用途", "挪用基金", "侵占基金"),
    ),
    ViolationType(
        "违规关联交易",
        aliases=("未披露关联交易", "利益输送", "关联交易价格不公允", "关联交易"),
    ),
    ViolationType(
        "未按规定托管",
        aliases=("未设托管机构", "未选合格托管机构", "未选任托管机构", "未聘请托管", "未办理托管"),
    ),
    ViolationType(
        "未按规定估值",
        aliases=("估值方法不当", "估值不及时", "未及时估值", "估值"),
    ),
    ViolationType(
        "非专业化运营",
        aliases=("兼营与私募基金管理无关的业务", "兼营存在利益冲突的业务", "兼营无关业务", "兼营"),
    ),
    ViolationType(
        "未尽勤勉尽责义务",
        aliases=(
            "未勤勉尽责",
            "未谨慎勤勉",
            "未尽管理人职责",
            "尽调不充分",
            "疏于管理",
            "未充分尽职调查",
        ),
        labels=((Dataset.CSRC, "未勤勉尽责"),),
    ),
    ViolationType(
        "内控缺失",
        aliases=(
            "内控制度",
            "内部控制",
            "合规风控体系",
            "岗位设置混乱",
            "文件资料保管不善",
            "妥善保存",
        ),
    ),
    ViolationType(
        "人员与场所违规",
        aliases=(
            "办公场所",
            "经营场所",
            "人员配置不足",
            "人员配备不足",
            "高管任职",
            "合规风控人员",
        ),
    ),
    ViolationType(
        "未持续符合登记条件",
        aliases=("失联", "不再具备登记条件", "不符合登记条件", "资本金不足", "不符合登记要求"),
    ),
    ViolationType(
        "未配合自律管理",
        aliases=(
            "未配合监管",
            "未配合检查",
            "拒不配合",
            "提供虚假材料",
            "逾期不整改",
            "未按期整改",
            "未完成整改",
        ),
        labels=((Dataset.CSRC, "未配合监管"),),
    ),
    ViolationType("其他"),
)


def violation_candidates(dataset: Dataset | str | None) -> list[str]:
    """派生指定数据集的提取提示词候选类型（按数据集展示措辞）。"""
    target = dataset if isinstance(dataset, Dataset) else Dataset.parse(dataset)
    return [
        item.label_for(target)
        for item in _VIOLATION_DEFS
        if target is None or target in item.datasets
    ]


def violation_label(name: str, dataset: Dataset | str | None = None) -> str:
    """把 canonical（或别名）类型名转成指定数据集下的展示名。"""
    for item in _VIOLATION_DEFS:
        if name == item.name or name in item.aliases:
            return item.label_for(dataset)
    return name


def violations_text(raw: str | None, dataset: Dataset | str | None = None) -> str:
    """把多值违规字段归一后按数据集措辞拼成展示串（无内容时返回空串）。"""
    return "、".join(violation_label(name, dataset) for name in canonical_violations(raw))


#: 全部 canonical 类型（跨数据集合并统计、界面筛选项的权威顺序）
VIOLATION_TYPES: list[str] = [item.name for item in _VIOLATION_DEFS]

#: AMAC 提取提示词候选类型（由适用范围派生，勿手工维护）
VIOLATION_TYPES_AMAC: list[str] = violation_candidates(Dataset.AMAC)

#: CSRC 提取提示词候选类型（由适用范围派生，勿手工维护）
VIOLATION_TYPES_CSRC: list[str] = violation_candidates(Dataset.CSRC)

#: 处罚类别归类关键词，用于把自由文本处罚措施归入粗类别
PUNISHMENT_CATEGORIES: dict[str, list[str]] = {
    "警告": ["警告"],
    "公开谴责": ["公开谴责"],
    "暂停受理备案": ["暂停受理"],
    "撤销管理人登记": ["撤销"],
    "取消会员资格": ["取消会员"],
    "加入黑名单": ["黑名单"],
    "罚款": ["罚款"],
    "市场禁入": ["市场禁入", "禁入"],
    "责令改正": ["责令改正", "责令"],
    "出具警示函": ["警示函"],
    "监管谈话": ["监管谈话"],
    "其他": [],
}


def _match_fragment(fragment: str) -> str | None:
    """把单个片段匹配到 canonical 类型；无法命中时返回 ``None``。"""
    for item in _VIOLATION_DEFS:
        if item.name in _EXACT_MATCH_NAMES:
            if fragment == item.name:
                return item.name
        elif item.name in fragment:
            return item.name
    for item in _VIOLATION_DEFS:
        if item.name in _EXACT_MATCH_NAMES:
            continue
        for alias in item.aliases:
            if alias in fragment:
                return item.name
    return None


def canonical_violations(raw: str | None) -> list[str]:
    """归一化但不补「未分类」占位（供落库关联表使用）。"""
    return [item for item in normalize_violations(raw) if item != UNCLASSIFIED]


def normalize_violations(raw: str | None) -> list[str]:
    """把 ``violation_type`` 多值字段拆分并归一到 canonical 分类体系。

    处理流程：先按顿号 / 逗号 / 分号 / 中点拆分多值字段，再按冒号拆分
    「类型：子项」写法，逐片段做「包含 canonical 名 → 包含别名」两级匹配
    （例如「违规募集（向不合格投资者…）」「适当性管理不到位」都归为
    「违规募集」）；全部未命中时保留原片段，保证新类型不被丢弃。
    """
    result: list[str] = []
    for fragment in split_multi_value(raw):
        for part in _SUB_SPLIT.split(fragment):
            part = part.strip()
            if not part:
                continue
            matched = _match_fragment(part) or part
            if matched not in result:
                result.append(matched)
    return result or [UNCLASSIFIED]


def categorize_punishment(text: str) -> str:
    """把自由文本的处罚措施归入粗类别。"""
    for category, keywords in PUNISHMENT_CATEGORIES.items():
        if category == "其他":
            continue
        for keyword in keywords:
            if keyword in text:
                return category
    return "其他"


#: 针对性防控建议（键为 canonical 类型名）
VIOLATION_ADVICE: dict[str, list[str]] = {
    "信息披露违规": [
        "建立信息披露日历，按合同约定和监管要求定期披露",
        "确保投资者信息获取渠道畅通，及时更新联系方式",
        "披露内容做到真实、准确、完整，避免选择性披露",
    ],
    "操纵市场": [
        "严禁通过连续交易、约定交易等方式影响证券交易价格或交易量",
        "建立交易行为监控机制，对异常交易及时预警和留痕",
    ],
    "内幕交易": [
        "建立内幕信息知情人登记管理制度，严格信息隔离",
        "禁止利用未公开信息从事交易，员工买卖证券须事前申报",
    ],
    "违规募集": [
        "严格审查募集渠道资质，禁止委托无基金销售资格机构开展募集",
        "杜绝任何形式的保本保收益承诺，包括口头承诺和抽屉协议",
        "完善投资者适当性管理，确保风险评级与投资者风险承受能力匹配",
    ],
    "未按规定备案": [
        "建立基金产品备案台账，新设基金及时向协会办理备案手续",
        "指定专人跟踪备案进度，避免因人员变动导致备案遗漏",
        "定期核对已管理产品与已备案产品清单，确保无遗漏",
    ],
    "登记信息失实": [
        "确保登记备案信息真实、准确、完整，杜绝虚假填报",
        "重大事项变更时及时向协会提交变更申请",
        "定期核对从业人员管理系统与实际人员情况，确保一致",
    ],
    "违规投资运作": [
        "严格遵守基金合同约定的投资范围和投资限制",
        "建立投资决策审批流程，重大投资需经合规审查",
        "禁止开展资金池业务和通道业务，确保每只基金独立运作",
    ],
    "挪用基金财产": [
        "严格隔离基金财产与管理人自有财产",
        "禁止将基金财产用于担保、明股实债等非约定用途",
        "建立资金划拨双人复核机制，防范资金挪用风险",
    ],
    "违规关联交易": [
        "建立关联交易管理制度，关联交易需经合规审查和披露",
        "防范利益输送，确保关联交易价格公允",
    ],
    "未按规定托管": [
        "按照规定为私募基金选取合格托管机构",
        "确保基金资产由托管机构独立保管，避免资金混同",
    ],
    "未按规定估值": [
        "按照合同约定和行业规范制定估值方法",
        "定期由独立第三方进行估值核对",
    ],
    "非专业化运营": [
        "主营业务应清晰聚焦于私募基金管理，不得兼营无关业务",
        "严禁从事民间借贷、担保、保理、小额贷款等与私募基金管理无关的业务",
        "定期审查公司经营范围和实际业务，确保不存在利益冲突",
    ],
    "未尽勤勉尽责义务": [
        "切实履行谨慎勤勉义务，对投资标的进行充分尽职调查",
        "核实投资者与投资标的之间的关联关系，防范利益冲突",
        "主动管理基金财产，不得疏于管理或放任不管",
    ],
    "内控缺失": [
        "建立健全内部控制体系，确保合规风控人员独立履职",
        "禁止合规风控人员兼任投资等冲突职务",
        "完善档案管理制度，妥善保管募集、投资等业务资料",
    ],
    "人员与场所违规": [
        "确保办公场所独立，不得与关联方共用办公场地",
        "配备足够数量的专职人员，满足管理人最低人员要求",
        "高管任职须符合资格条件，禁止合规风控负责人兼任冲突职务",
    ],
    "未持续符合登记条件": [
        "定期自查管理人登记条件持续符合情况，包括人员、场所、资本金等",
        "发生重大变更时及时向协会报告并更新登记信息",
        "对不再符合条件的情况及时整改，避免被撤销登记",
    ],
    "未配合自律管理": [
        "积极配合协会与监管机构检查，如实提供相关材料",
        "对提出的整改要求在规定期限内完成整改",
        "建立与监管机构的常态化沟通机制",
    ],
    "其他": [
        "加强从业人员合规培训，定期组织法规学习",
        "建立合规自查机制，及时发现和整改问题",
    ],
}


def advice_for(violation: str) -> list[str]:
    """返回某违规类型的防控建议，未知类型回落到「其他」。

    入参可以是 canonical 名或任一别名（「未勤勉尽责」「适当性管理不到位」等）。
    """
    direct = VIOLATION_ADVICE.get(violation)
    if direct:
        return direct
    for name in normalize_violations(violation):
        advice = VIOLATION_ADVICE.get(name)
        if advice:
            return advice
    return VIOLATION_ADVICE["其他"]
