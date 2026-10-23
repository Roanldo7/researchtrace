"""Reviewed evidence selection, bounded writing context and deterministic composition."""
import copy
import json
import re

SHARE_SCOPE = '乘联会中国新能源乘用车国内零售；厂商统计边界，非上市集团全球销量'
SCOPES = {
 '中国汽车行业': '中汽协汽车行业销量口径，含出口、乘用车及商用车；非国内零售',
 '比亚迪': '合并报表人民币；含汽车及手机部件组装等业务；净利润为归属于股东',
 '理想汽车': '企业全年交付；财务为合并报表人民币、未经审计',
 '蔚来': '企业全年交付，含蔚来及乐道；财务为合并报表人民币、未经审计',
 '宁德时代': '企业发布的产品特性；非独立实测或全市场普及率'}
TECH_SCOPE = '企业新品发布描述；非自动驾驶能力评测'
MAKERS = ['比亚迪', '特斯拉中国', '广汽埃安']
COMPANIES = ['比亚迪', '理想汽车', '蔚来']
KEY_FIELDS = ('entity', 'metric', 'period', 'scope', 'value_type', 'unit')


def comparable_key(f):
    return tuple(f[k] for k in KEY_FIELDS)


def spec(entity, metric, period='2024'):
    scope, kind, unit = SCOPES.get(entity), 'actual', '亿元'
    if metric in ('国内零售份额', '份额变化'):
        scope, unit = SHARE_SCOPE, '%' if metric == '国内零售份额' else '个百分点'
    elif entity == '中国汽车行业':
        unit = '万辆' if metric == '新能源汽车销量' else '%'
    elif metric in ('电池技术', '智能化发布', '交付计划'):
        scope = SCOPES['宁德时代'] if entity == '宁德时代' else TECH_SCOPE
        unit = ''
        if metric == '交付计划':
            kind = 'announced_plan'
    elif metric.endswith('同比'):
        unit = '%'
    return (entity, metric, period, scope, kind, unit)


def pick(facts, entity, metric, period='2024'):
    key = spec(entity, metric, period)
    found = [f for f in facts if comparable_key(f) == key]
    if len(found) > 1:
        raise ValueError('写作上下文存在重复可比键')
    return found[0] if found else None


def display(f):
    v = f['value']
    return (f'{v:,.4f}'.rstrip('0').rstrip('.') if isinstance(v, (int, float)) else v) + f['unit']


def derive(facts):
    result = []
    for i, company in enumerate(MAKERS, 1):
        previous, current = (pick(facts, company, '国内零售份额', y) for y in ('2023', '2024'))
        if previous and current:
            result.append(dict(zip(KEY_FIELDS, spec(company, '份额变化', '2023→2024')),
                fact_id=f'D{i:02}', value=round(current['value']-previous['value'], 4), category='number',
                input_fact_ids=[previous['fact_id'], current['fact_id']],
                formula=f"{current['fact_id']} - {previous['fact_id']} (percentage points)",
                source_ids=sorted(set(previous['source_ids']+current['source_ids'])),
                synthetic_test=False, status='confirmed', derived=True))
    return result


def section_specs(focus='industry'):
    market = [spec('中国汽车行业', m) for m in ('新能源汽车销量', '销量同比', '新车销量占比')]
    shares = [spec(e, '国内零售份额', y) for e in MAKERS for y in ('2023', '2024')]
    trends = [spec(e, '份额变化', '2023→2024') for e in MAKERS]
    tech = [spec('宁德时代', '电池技术'), spec('蔚来', '智能化发布'), spec('蔚来', '交付计划', '2025-03')]
    finance = [spec(e, m) for e, metrics in [
        ('比亚迪', ['营业收入', '归母净利润']),
        ('理想汽车', ['营业收入', '营业收入同比', '净利润', '净利润同比']),
        ('蔚来', ['营业收入', '净亏损', '净亏损同比'])] for m in metrics]
    if focus == 'company':
        return {'研究范围': [market[0]], '营收对比': [spec(e, '营业收入') for e in COMPANIES],
                '盈利对比': [spec('比亚迪','归母净利润'),spec('理想汽车','净利润'),spec('蔚来','净亏损')],
                '增长与压力': [spec('理想汽车','营业收入同比'),spec('理想汽车','净利润同比'),spec('蔚来','净亏损同比')],
                '观察方向': [spec('比亚迪','归母净利润'),spec('宁德时代','电池技术')], '风险提示': []}
    return {'市场概况': market, '竞争格局': shares+trends, '技术趋势': tech,
            '重点公司分析': finance, '投资机会': [market[0], tech[0], spec('比亚迪', '归母净利润')], '风险提示': []}


def assess(facts, focus='industry'):
    requested = set(k for keys in section_specs(focus).values() for k in keys)
    available = {comparable_key(f) for f in facts}
    missing = [' / '.join((k[0], k[1], k[2])) for k in sorted(requested-available)]
    core = [spec(e, '营业收入') for e in COMPANIES]
    if focus == 'industry':
        core = [spec('中国汽车行业', '新能源汽车销量')] + [spec(e, '国内零售份额') for e in MAKERS] + core
    absent = [' / '.join((k[0], k[1], k[2])) for k in core if k not in available]
    if focus == 'industry' and not any(spec(e,m) in available for e,m in [('宁德时代','电池技术'),('蔚来','智能化发布')]):
        absent.append('至少一项2024技术发布事实')
    if focus == 'company' and not any(spec(e,m) in available for e,m in [('比亚迪','归母净利润'),('理想汽车','净利润'),('蔚来','净亏损')]):
        absent.append('至少一项2024盈利或亏损事实')
    return dict(sufficient=bool(facts) and not absent, core_missing=absent, missing_fields=missing,
                supported_companies=sum(spec(e,'营业收入') in available for e in COMPANIES))


def build_context(plan, facts, documents, scenario, char_budget=12000, fragment_budget=24):
    """Only approved, non-synthetic facts enter. Budgets cover each full section JSON."""
    facts = [copy.deepcopy(f) for f in facts if f['status']=='confirmed' and not f['synthetic_test']]
    by_id = {f['fact_id']:f for f in facts}
    docs = {d['source_id']:d for d in documents}
    sections, logs = {}, []
    for name, keys in section_specs(plan.get('focus', 'industry')).items():
        selected, fragments, skipped = [], {}, []
        for key in keys:
            f = next((f for f in facts if comparable_key(f)==key), None)
            if not f:
                continue
            # Derived values only survive if both inputs fit in this same section.
            if f.get('derived') and not set(f['input_fact_ids']) <= {x['fact_id'] for x in selected}:
                skipped.append({'fact_id':f['fact_id'], 'reason':'派生输入不在章节上下文'})
                continue
            additions = dict(fragments)
            primitives = [by_id[x] for x in f['input_fact_ids']] if f.get('derived') else [f]
            for base in primitives:
                for sid in base['source_ids']:
                    d = docs[sid]; locator = base['excerpt_locator']
                    field = next(x for x in d['fields'] if x['metric']==base['metric'] and x['locator']==locator
                                 and x.get('entity', d['entity'])==base['entity']
                                 and x.get('period', d['period'])==base['period'])
                    fragment = re.search(field['pattern'], d['excerpts'][locator]).group(0)
                    additions[sid+':'+base['fact_id']] = dict(source_id=sid, publisher=d['publisher'], title=d['title'],
                        published_at=d['published_at'], source_url=d['source_url'], locator=locator,
                        fact_id=base['fact_id'], text=fragment)
            candidate = dict(facts=selected+[f], fragments=list(additions.values()))
            count = len(json.dumps(candidate, ensure_ascii=False))
            if count>char_budget or len(additions)>fragment_budget:
                skipped.append({'fact_id':f['fact_id'], 'reason':'章节字符或片段预算不足'})
                continue
            selected.append(f); fragments=additions
        section = dict(facts=selected, fragments=list(fragments.values()))
        sections[name]=section
        logs.append(dict(section=name, char_budget=char_budget, used_chars=len(json.dumps(section,ensure_ascii=False)),
                         fragment_budget=fragment_budget, used_fragments=len(fragments), skipped=skipped))
    used = {f['fact_id']:f for s in sections.values() for f in s['facts']}
    evidence = assess(list(used.values()), plan.get('focus', 'industry'))
    context = dict(topic=plan['topic'], period=plan['period'], observation_date=plan['observation_date'],
                   focus=plan.get('focus', 'industry'),
                   scenario=scenario, sections=sections, missing_fields=evidence['missing_fields'])
    return context, logs, evidence


def compose(context):
    if context.get('focus') == 'company':
        return compose_company(context)
    def get(section, entity, metric, period='2024'):
        return pick(context['sections'][section]['facts'], entity, metric, period)
    def block(text, fs=()):
        return dict(type='paragraph', text=text, fact_ids=[f['fact_id'] for f in fs if f])
    sections = []
    market, refs = '', []
    for metric in ('新能源汽车销量','销量同比','新车销量占比'):
        f=get('市场概况','中国汽车行业',metric)
        market += f'{metric}为{display(f)}。' if f else f'{metric}未采用，暂缺。'
        if f: refs.append(f)
    market += '中汽协行业销量含出口与商用车，不等于国内零售渗透率。'
    sections.append(dict(title='一、市场概况',blocks=[block(market,refs)]))
    rows=[]
    for company in sorted(MAKERS,key=lambda e:get('竞争格局',e,'国内零售份额')['value'],reverse=True):
        previous=get('竞争格局',company,'国内零售份额','2023');current=get('竞争格局',company,'国内零售份额')
        delta=get('竞争格局',company,'份额变化','2023→2024')
        rows.append(dict(cells=[company, display(previous) if previous else '未采用', display(current),
            (('+' if delta['value']>0 else '')+display(delta)) if delta else '缺输入'],
            fact_ids=[f['fact_id'] for f in (previous,current,delta) if f]))
    sections.append(dict(title='二、竞争格局',blocks=[
        block('乘联会中国新能源乘用车国内零售，厂商边界；三家按2024份额作样本内排序，非完整全国榜单。两期均为CnEVPost转引同一体系。'),
        dict(type='table',headers=['厂商','2023','2024','变化'],rows=rows),
        block('变化=2024份额−2023份额（百分点），由已披露的四舍五入份额相减。公司分析另选比亚迪、理想、蔚来，考察不同盈利状态。')]))
    tech=[]
    battery=get('技术趋势','宁德时代','电池技术');launch=get('技术趋势','蔚来','智能化发布');plan=get('技术趋势','蔚来','交付计划','2025-03')
    if battery:tech.append(block('2024年宁德时代发布神行PLUS，宣称磷酸铁锂1000km续航与4C超充；这是企业规格，非独立性能验证。',[battery]))
    if launch:tech.append(block('蔚来2024年发布ET9并介绍智能系统与辅助驾驶。',[launch]))
    if plan:tech.append(block('2025年1月公告预计同年3月起交付ET9，属于计划；本快照不证明已交付。',[plan]))
    sections.append(dict(title='三、技术趋势',blocks=tech))
    companies=[]
    for e in COMPANIES:
        rev=get('重点公司分析',e,'营业收入');fs=[rev];text=f'2024年营收{display(rev)}。'
        if e=='比亚迪':
            profit=get('重点公司分析',e,'归母净利润')
            if profit:text+=f'归母净利{display(profit)}，已具盈利规模。';fs.append(profit)
            text+='合并口径含手机部件等业务，不能等同汽车盈利；关注业务结构与利润持续性。'
        elif e=='理想汽车':
            rg=get('重点公司分析',e,'营业收入同比');p=get('重点公司分析',e,'净利润');pg=get('重点公司分析',e,'净利润同比')
            if rg:text+=f'营收同比{display(rg)}。';fs.append(rg)
            if p:text+=f'净利{display(p)}。';fs.append(p)
            if pg:text+=f'净利同比{display(pg)}。';fs.append(pg)
            text+=('增收未带来净利同步增长，关注费用与产品定价。' if rg and pg and rg['value']>0 and pg['value']<0 else '关注盈利兑现。')+'财务未经审计。'
        else:
            loss=get('重点公司分析',e,'净亏损');lg=get('重点公司分析',e,'净亏损同比')
            if loss:text+=f'净亏损{display(loss)}。';fs.append(loss)
            if lg:text+=f'亏损同比扩大{display(lg)}。';fs.append(lg)
            text+=('规模尚未转为盈利，关注成本与资金消耗。' if loss else '关注盈利兑现。')+'财务未经审计，净亏损非归母口径。'
        companies.append(dict(type='subsection',title=f'4.{len(companies)+1} {e}',text=text,fact_ids=[f['fact_id'] for f in fs if f]))
    sections.append(dict(title='四、重点公司分析',blocks=companies))
    opportunities=[]
    profit=get('投资机会','比亚迪','归母净利润');battery=get('投资机会','宁德时代','电池技术')
    if profit:opportunities.append(block('观察方向：有盈利基础的整车企业；继续核验价格竞争下的利润持续性与估值，不能从盈利直接推出股价空间。',[profit]))
    if battery:opportunities.append(block('观察方向：超充电池与补能配套；继续核验订单、成本和利用率，发布不等于商业化收益。',[battery]))
    if not opportunities:opportunities.append(block('投资方向所需证据未采用，本节不形成方向。'))
    sections.append(dict(title='五、投资机会',blocks=opportunities))
    sections.append(dict(title='六、风险提示',blocks=[block('关注价格竞争、需求与政策变化、技术兑现及资金风险；公司财务与厂商零售不可混除。未作估值或收益保证。')]))
    used=set()
    for s in sections:
        for b in s['blocks']:
            used.update(b.get('fact_ids',[]))
            for row in b.get('rows',[]):used.update(row['fact_ids'])
    facts={f['fact_id']:f for s in context['sections'].values() for f in s['facts']}
    source_ids={sid for fid in used for sid in facts[fid]['source_ids']}
    docs={}
    for s in context['sections'].values():
        for frag in s['fragments']:
            if frag['source_id'] not in source_ids:continue
            d=docs.setdefault(frag['source_id'],{k:frag[k] for k in ('source_id','publisher','title','published_at','source_url')})
            excerpts=d.setdefault('excerpts',{})
            excerpts[frag['locator']]=excerpts.get(frag['locator'],'') + frag['text'] + '；'
    return dict(title='2024年新能源汽车行业竞争格局分析',subtitle='离线Mock研究摘要｜2024全年｜观察截止2025-03-31（事后研究）',
        sections=sections,sources=sorted(docs.values(),key=lambda d:d['source_id']),used_fact_ids=sorted(used),
        scenario_label='异常测试产物：已排除合成错误候选。' if context['scenario']=='exception' else '')


def compose_company(context):
    def get(section, entity, metric):
        return pick(context['sections'][section]['facts'], entity, metric)
    def block(text, facts=()):
        return dict(type='paragraph', text=text, fact_ids=[f['fact_id'] for f in facts if f])
    market = get('研究范围','中国汽车行业','新能源汽车销量')
    sections = [dict(title='一、研究范围',blocks=[block(
        '观察2024年新能源汽车行业及三家公司。行业销量'+(f'为{display(market)}' if market else '缺少已采用数据')+'；公司财务采用各自合并口径，不把行业销量当作企业份额。',[market])])]
    revenue = [get('营收对比',e,'营业收入') for e in COMPANIES]
    sections.append(dict(title='二、营收对比',blocks=[block('；'.join(
        f'{e}营收{display(f)}' if f else f'{e}营收缺失' for e,f in zip(COMPANIES,revenue))+'。营业收入含各公司不同业务，不等于汽车业务收入。',revenue)]))
    profits = [get('盈利对比',e,m) for e,m in [('比亚迪','归母净利润'),('理想汽车','净利润'),('蔚来','净亏损')]]
    sections.append(dict(title='三、盈利对比',blocks=[block('；'.join(
        f'{e}{m}{display(f)}' if f else f'{e}{m}缺失' for (e,m),f in zip(
            [('比亚迪','归母净利润'),('理想汽车','净利润'),('蔚来','净亏损')],profits))+'。利润归属与亏损口径不完全相同，不据此直接排名利润率。',profits)]))
    changes=[get('增长与压力',e,m) for e,m in [('理想汽车','营业收入同比'),('理想汽车','净利润同比'),('蔚来','净亏损同比')]]
    sections.append(dict(title='四、增长与压力',blocks=[block('；'.join(
        f'{m}{display(f)}' if f else f'{m}缺失' for m,f in zip(['理想营收同比','理想净利润同比','蔚来净亏损同比'],changes))+'。理想与蔚来披露为当时未经审计口径，后续应核对费用与现金压力。',changes)]))
    profit=get('观察方向','比亚迪','归母净利润');battery=get('观察方向','宁德时代','电池技术')
    sections.append(dict(title='五、观察方向',blocks=[block('盈利持续性与补能技术商业化是后续观察方向；已披露利润和产品发布均不能直接推出估值或收益。',[profit,battery])]))
    sections.append(dict(title='六、风险提示',blocks=[block('关注价格竞争、不同会计口径、技术兑现和资金压力；本摘要不作个性化买卖判断。')]))
    used={fid for section in sections for b in section['blocks'] for fid in b['fact_ids']}
    facts={f['fact_id']:f for data in context['sections'].values() for f in data['facts']}
    source_ids={sid for fid in used for sid in facts[fid]['source_ids']}
    docs={}
    for data in context['sections'].values():
        for frag in data['fragments']:
            sid=frag['source_id']
            if sid not in source_ids:continue
            d=docs.setdefault(sid,{k:frag[k] for k in ('source_id','publisher','title','published_at','source_url')})
            excerpts=d.setdefault('excerpts',{})
            excerpts[frag['locator']]=excerpts.get(frag['locator'],'')+frag['text']+'；'
    return dict(title='2024年新能源汽车重点公司财务对比',
        subtitle='规则生成研究摘要｜2024全年｜观察截止2025-03-31（事后研究）',sections=sections,
        sources=sorted(docs.values(),key=lambda d:d['source_id']),used_fact_ids=sorted(used),
        scenario_label='异常测试产物：已排除合成错误候选。' if context['scenario']=='exception' else '')


def markdown(report, facts):
    lookup={f['fact_id']:f for f in facts}
    def refs(ids):return ' '.join(f'[{sid}]' for sid in sorted({sid for fid in ids for sid in lookup[fid]['source_ids']}))
    lines=['# '+report['title'],'',report['subtitle'],'']
    if report['scenario_label']:lines += [report['scenario_label'],'']
    for section in report['sections']:
        lines += ['## '+section['title'],'']
        for b in section['blocks']:
            if b['type']=='table':
                lines += ['| '+' | '.join(b['headers'])+' |','| '+' | '.join(['---']*len(b['headers']))+' |']
                for row in b['rows']:lines.append('| '+' | '.join(row['cells'])+' '+refs(row['fact_ids'])+' |')
            else:
                if b['type']=='subsection':lines += ['### '+b['title'],'']
                lines.append(b['text']+' '+refs(b['fact_ids']))
            lines.append('')
    lines += ['## 数据来源','']
    for d in report['sources']:lines.append(f"- [{d['source_id']}] {d['publisher']}：{d['title']}（{d['published_at']}）")
    lines += ['']+[f"[{d['source_id']}]: {d['source_url']}" for d in report['sources']]
    return '\n'.join(lines)+'\n'


def check(report, facts, text, focus='industry'):
    lookup={f['fact_id']:f for f in facts};docs={d['source_id']:d for d in report['sources']}
    def located(fid):
        if fid not in lookup:return False
        f=lookup[fid]
        if f['synthetic_test'] or f['status']!='confirmed':return False
        if f.get('derived'):return bool(f['input_fact_ids']) and all(located(x) for x in f['input_fact_ids'])
        return bool(f['source_ids']) and all(sid in docs and f['excerpt_locator'] in docs[sid]['excerpts'] for sid in f['source_ids'])
    used=report['used_fact_ids']; valid=all(located(fid) for fid in used) if used else None
    visible=re.sub(r'^\[S\d+\]:.*$','',text,flags=re.M)
    count=len(re.sub(r'[\s#|*\[\]`-]','',visible))
    chunks=re.split(r'^## ',visible,flags=re.M)[1:]
    lengths=[len(re.sub(r'[\s#|*\[\]`-]','',c)) for c in chunks[:6]]
    company_blocks=[b for b in report['sections'][3]['blocks'] if b['type']=='subsection']
    company_ok=all(len(re.sub(r'\s','',b['title']+b['text']))<=150 for b in company_blocks)
    section_ok=len(lengths)==6 and all(n<=cap for n,cap in zip(lengths,[200,300,200,450,200,100]))
    safe=all(not lookup[fid]['synthetic_test'] for fid in used) if used else None
    sufficient=assess(facts, focus)['sufficient']
    return dict(valid_evidence=valid,no_synthetic_facts=safe,visible_characters=count,character_limit=1500,
        section_characters=lengths,section_limits_ok=section_ok and company_ok,six_sections=len(report['sections'])==6,
        company_sections=len(company_blocks),supported_company_sections=sum(bool(b['fact_ids']) for b in company_blocks),
        evidence_sufficient=sufficient,format_valid=count<=1500 and section_ok and company_ok,
        passed=bool(valid and safe and sufficient and count<=1500 and section_ok and company_ok))
