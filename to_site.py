#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 outputs/en/sample-*.md 转成站点可直接构建的 content/en/posts/*.md。

样张文件是「给人读的稿子」：带 H1、署名行、页脚声明。
站点文章是「给模板喂的内容」：标题/日期/分类进 frontmatter，正文里不能再有 H1。

MAP 的 words 字段是中文原文字数，只作记录 —— 站点上显示的是英文实际词数，
由脚本从正文数出来，写进 frontmatter 的 words。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # .../repo
ROOT = os.path.dirname(HERE)                                # 工作目录
SRC = os.path.join(ROOT, 'outputs', 'en')
DST = os.path.join(HERE, 'content', 'en', 'posts')

# series 只用于排序（build.py 的 sort key），不上页面。
# 统一一个值 + 显式 order，比按系列名分组更可控 ——
# 系列名一旦变化，字母序会把新批次插到中间去。
SERIES = 'Foundations of Yixue'
CAT_KEY = '易学基础'
CAT_EN = 'Foundations of Yixue'

# (样张文件, slug, 标题, 日期, 标签, 中文原文字数, 原文文件名, 系列内顺序)
MAP = [
    ('sample-01-tiangan-dizhi.md',
     'what-exactly-are-the-heavenly-stems-and-earthly-branches-1',
     'What Exactly Are the Heavenly Stems and Earthly Branches? (Part 1)',
     '2021-05-23', ['Bazi', 'Tiangan Dizhi', 'Yixue basics'], 4534,
     '天干地支到底是什么东西？（一）-370.md', 1),
    ('sample-02-tiangan-dizhi-2.md',
     'what-exactly-are-the-heavenly-stems-and-earthly-branches-2',
     'What Exactly Are the Heavenly Stems and Earthly Branches? (Part 2)',
     '2021-05-24', ['Bazi', 'Jupiter', 'Saturn', 'Tiangan Dizhi'], 4312,
     '天干地支到底是什么东西？（二）-369.md', 2),
    ('sample-03-tiangan-dizhi-3.md',
     'what-exactly-are-the-heavenly-stems-and-earthly-branches-3',
     'What Exactly Are the Heavenly Stems and Earthly Branches? (Part 3)',
     '2021-05-25', ['Feng shui', 'Tiangan Dizhi', 'Direction'], 7087,
     '天干地支到底是什么东西？（三）-368.md', 3),
    ('sample-04-ten-stems-astronomy.md',
     'astronomical-origin-of-the-ten-heavenly-stems',
     'What Is the Astronomical Origin of the Ten Heavenly Stems?',
     '2020-02-04', ['Tiangan Dizhi', 'Jupiter', 'Saturn', 'Astronomy'], 4290,
     '十天干的天文学科学来源是什么？-586.md', 4),
    ('sample-05-twelve-branches-astronomy.md',
     'astronomical-origin-of-the-twelve-earthly-branches',
     'What Is the Astronomical Origin of the Twelve Earthly Branches?',
     '2020-02-02', ['Tiangan Dizhi', 'Jupiter', 'Astronomy', 'Zodiac'], 3722,
     '十二地支的天文学科学来源是什么？-589.md', 5),
    ('sample-06-yin-yang-of-stems-branches.md',
     'yin-and-yang-in-the-stems-and-branches',
     'What Is the Real Meaning of Yin and Yang in the Stems and Branches?',
     '2020-08-08', ['Yin Yang', 'Bazi', 'Tiangan Dizhi'], 1702,
     '干支阴阳属性的本质是什么？-459.md', 6),
    ('sample-07-generation-and-control.md',
     'how-the-stems-and-branches-generate-and-control',
     'How the Stems and Branches Generate and Control One Another',
     '2021-05-27', ['Bazi', 'Tiangan Dizhi', 'Wuxing'], 3527,
     '天干地支的生克问题-366.md', 7),
    ('sample-08-wuxing-theorem-not-axiom.md',
     'is-the-five-element-sequence-a-theorem-or-an-axiom',
     'Is the Sequence of the Five Elements a Theorem or an Axiom?',
     '2018-03-03', ['Wuxing', 'Theory', 'Science'], 3494,
     '五行相生的顺序是定理而非公理-614.md', 8),
    ('sample-09-zodiac-animals-and-branches.md',
     'what-do-the-zodiac-animals-have-to-do-with-the-branches',
     'What Do the Twelve Zodiac Animals Have to Do with the Earthly Branches?',
     '2021-05-26', ['Tiangan Dizhi', 'Zodiac', 'Theory'], 2857,
     '天干地支与生肖属相的关系-367.md', 9),
    ('sample-10-twenty-four-directions.md',
     'five-element-properties-of-the-twenty-four-directions',
     'The Five-Element Properties of the Twenty-Four Compass Directions',
     '2021-05-05', ['Feng shui', 'Direction', 'Wuxing'], 2000,
     '风水上24个方位的五行属性-387.md', 10),
    ('sample-11-why-students-get-cheated.md',
     'why-students-of-the-yijing-keep-getting-cheated',
     'Why Do Students of the Yijing Keep Getting Cheated by the Masters?',
     '2021-01-25', ['Yixue basics', 'Study', 'Critique'], 3404,
     '为什么学习易经的人总是被大师们欺骗？-417.md', 11),
    ('sample-12-physics-of-stems-branches.md',
     'the-physical-nature-of-the-heavenly-stems-and-earthly-branches',
     'What Is the Physical Nature of the Heavenly Stems and Earthly Branches?',
     '2020-02-26', ['Tiangan Dizhi', 'Energy', 'Theory'], 6150,
     '天干地支的物理学本质是什么？-561.md', 12),
    ('sample-13-charts-with-one-or-two-elements.md',
     'reading-a-chart-with-only-one-or-two-elements',
     'How Do You Read a Chart With Only One or Two Elements?',
     '2018-01-27', ['Bazi', 'Wuxing', 'Chart reading'], 3593,
     '命局只有一个或两个五行时怎么算命？-629.md', 13),
    ('sample-14-choosing-the-yongshen-in-liuyao.md',
     'how-to-choose-the-yongshen-in-liuyao-divination',
     'How Do You Choose the "Yongshen" in Liuyao Divination?',
     '2017-11-13', ['Liuyao', 'Divination', 'Yongshen'], 4268,
     '六爻预测如何取“用神”？-662.md', 14),
    ('sample-15-yin-yang-energy-in-feng-shui.md',
     'movement-laws-of-the-harmonized-yin-yang-qi',
     'What Are the Movement Laws of the Harmonized Yin–Yang Qi in Feng Shui?',
     '2020-02-20', ['Feng shui', 'Yin Yang', 'Theory'], 5130,
     '风水的“阴阳冲和之气”的运动规律是什么？-570.md', 15),
    ('sample-16-truth-of-liuyao-theory.md',
     'where-does-the-truth-of-liuyao-prediction-lie',
     'Where Is the Truth in "Liuyao Prediction Theory"?',
     '2017-11-12', ['Liuyao', 'Divination', 'Theory', 'Critique'], 6374,
     '“六爻预测学”的真理何在？-663.md', 16),
    ('sample-17-seven-problems-of-liuyao.md',
     'where-is-the-real-theory-of-liuyao-prediction',
     'Where Is the Real Theory of Liuyao Prediction?',
     '2023-10-19', ['Liuyao', 'Divination', 'Theory'], 4919,
     '真理的“六爻预测学”理论在哪里？-302.md', 17),
    ('sample-18-shenbing-qimen-the-only-real-qimen.md',
     'shenbing-qimen-the-only-real-qimen-dunjia',
     'Shenbing Qimen Is the Real Qimen Dunjia — Every Other School Is a Jianghu Fake',
     '2023-01-03', ['Qimen', 'Theory', 'Critique'], 6068,
     '《神兵奇门遁甲》才是真正的奇门遁甲，其它所有奇门遁甲流派都是江湖伪奇门！-323.md', 18),
    ('sample-19-is-qimen-the-third-eye.md',
     'is-qimen-dunjia-the-feng-shui-masters-third-eye',
     'Is Qimen Dunjia the Feng Shui Master\'s Third Eye?',
     '2018-01-07', ['Qimen', 'Feng shui', 'Critique'], 2462,
     '“奇门遁甲”是风水师的第三只眼睛吗？-632.md', 19),
    ('sample-20-shenbing-qimen-three-cases.md',
     'shenbing-qimen-three-cases-read-before-the-event',
     'Shenbing Qimen in Practice: Three Cases',
     '2024-06-05', ['Qimen', 'Case studies'], 1795,
     '《神兵奇门》实测案例分享-288.md', 20),
    ('sample-21-what-is-the-taiji-halo.md',
     'what-exactly-is-the-taiji-halo',
     'What Exactly Is the "Taiji Halo"?',
     '2017-11-14', ['Feng shui', 'Taiji', 'Dianxue'], 6009,
     '“太极晕”到底是什么？-661.md', 21),
    ('sample-22-yin-yang-dragons-photo-walkthrough.md',
     'yin-and-yang-dragons-the-open-face-and-the-taiji-halo',
     'Yin Dragons, Yang Dragons, the Open Face and the Taiji Halo',
     '2020-05-03', ['Feng shui', 'Dianxue', 'Taiji'], 2936,
     '传统风水学之“阴龙、阳龙、开面处、太极晕”实地照片详解-524.md', 22),
    ('sample-23-what-can-feng-shui-predict.md',
     'what-can-feng-shui-and-the-yijing-actually-predict',
     'What Can Feng Shui and the Yijing Actually Predict?',
     '2017-11-01', ['Feng shui', 'Divination', 'Practice'], 2425,
     '“风水和易经”能预测什么人事？-673.md', 23),
    ('sample-24-what-i-got-from-the-lu-school.md',
     'what-i-got-from-studying-the-lu-school-of-natural-science-yixue',
     'What I Got From Studying the Lü School of Natural-Science Yixue',
     '2023-10-06', ['Bazi', 'Feng shui'], 3396,
     '学习吕文艺老师自然科学易经的收获和心得-306.md', 24),
]

# 第二批：实战层。每条多带三个字段（series, cat_key, cat_en），
# 因为同一批里分类要换，不能再用模块级常量。
# (样张文件, slug, 标题, 日期, 标签, 中文原文字数, 原文文件名, 分类内顺序,
#  series, cat_key, cat_en)
MAP2 = [
    ('sample-25-what-is-feng-shui-revisited.md',
     'what-is-feng-shui-revisited',
     'What Is Feng Shui? Revisited',
     '2020-01-27', ['Feng shui', 'Environment', 'Time'], 5920,
     '再谈“什么是风水？”-598.md', 1,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-26-what-is-a-feng-shui-master-looking-for.md',
     'what-is-a-feng-shui-master-actually-looking-for',
     'What Is a Feng Shui Master Actually Looking For?',
     '2020-01-28', ['Feng shui', 'Long mai', 'Dragon point'], 6623,
     '看风水到底在寻找什么？-597.md', 2,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-27-what-is-a-true-dragon-point.md',
     'what-is-a-true-dragon-point',
     'What Is a True “Dragon Point”?',
     '2017-12-19', ['Feng shui', 'Dragon point', 'Dianxue'], 5270,
     '什么是真正的“真龙穴”？-641.md', 3,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-28-core-secret-of-seeking-the-dragon-1.md',
     'the-core-secret-of-seeking-the-dragon-1',
     'The Core Secret of Seeking the Dragon and Setting the Point (Part 1)',
     '2017-12-23', ['Feng shui', 'Long mai', 'Dianxue'], 6038,
     '寻龙点穴的核心秘密是什么？（一）-639.md', 4,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-29-core-secret-of-seeking-the-dragon-2.md',
     'the-core-secret-of-seeking-the-dragon-2',
     'The Core Secret of Seeking the Dragon and Setting the Point (Part 2)',
     '2017-12-24', ['Feng shui', 'Long mai', 'Dianxue'], 4731,
     '寻龙点穴的核心秘密是什么？（二）-638.md', 5,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-30-dragon-vein-canopy-gorge-breath.md',
     'the-dragon-vein-passing-the-canopy-the-gorge-and-the-breath',
     'The Dragon Vein: Passing the Canopy, Crossing the Gorge, Binding the Breath',
     '2020-03-15', ['Feng shui', 'Long mai', 'Theory'], 4564,
     '传统风水学之龙脉的穿帐、过峡、束气-543.md', 6,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-31-is-a-cut-dragon-vein-bad-luck.md',
     'is-a-cut-dragon-vein-always-inauspicious',
     'Is a Cut Dragon Vein Always Bad Luck?',
     '2017-12-29', ['Feng shui', 'Long mai', 'Critique'], 4495,
     '“龙脉”被挖断了就一定是不吉利吗？-634.md', 7,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-32-where-vein-qi-accumulates.md',
     'where-vein-qi-accumulates-in-the-strata',
     'Where in the Strata Does Vein Energy Accumulate?',
     '2020-05-12', ['Feng shui', 'Dianxue', 'Geology'], 5299,
     '脉气能量在地层中的什么地方聚积-516.md', 8,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-33-precautions-at-the-focal-point.md',
     'precautions-when-setting-the-focal-point',
     'Precautions When Setting the Focal Point',
     '2020-05-16', ['Feng shui', 'Dianxue', 'Focal point'], 4823,
     '焦点穴点穴时需要注意的事项-513.md', 9,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-34-twenty-four-sand-burial-methods.md',
     'the-twenty-four-sand-burial-methods-in-photographs',
     'The Twenty-Four Sand Burial Methods, in Photographs',
     '2020-05-05', ['Feng shui', 'Dianxue', 'Photographs'], 3337,
     '传统风水学之“二十四砂葬法”实地图片详解-522.md', 10,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-35-four-hollow-mound-point-methods.md',
     'the-four-hollow-and-mound-point-methods-in-photographs',
     'The Four Point Methods — Hollow, Pincer, Breast and Mound — in Photographs',
     '2020-04-30', ['Feng shui', 'Dianxue', 'Photographs'], 1595,
     '传统风水学之“窝钳乳突”四大穴法实地图片详解-527.md', 11,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-36-focal-point-in-flat-country.md',
     'the-focal-point-in-flat-country',
     'The Focal Point in Flat Country',
     '2020-06-24', ['Feng shui', 'Dianxue', 'Plains'], 3363,
     '平洋地的焦点穴-482.md', 12,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-37-telling-sand-from-water-1.md',
     'telling-sand-from-water-1',
     'Telling Sand From Water (Part 1)',
     '2020-05-23', ['Feng shui', 'Sha shui', 'Theory'], 8024,
     '砂水鉴别问题（一）-508.md', 13,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-38-setting-the-orientation.md',
     'setting-the-orientation',
     'Setting the Orientation in Natural-Science Feng Shui',
     '2020-06-16', ['Feng shui', 'Orientation', 'Theory'], 5856,
     '自然科学风水学之立向-488.md', 14,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-39-can-the-fenjin-method-be-applied.md',
     'can-the-fenjin-method-actually-be-applied',
     'Can the “Sitting and Facing Fenjin” Theory Actually Be Applied?',
     '2017-11-16', ['Feng shui', 'Orientation', 'Critique'], 2554,
     '风水“坐向分金”理论具有可操作性吗？-659.md', 15,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-40-sand-and-water-in-24-directions.md',
     'sand-and-water-in-the-twenty-four-directions',
     'Reading Sand and Water in the Twenty-Four Directions',
     '2021-05-02', ['Feng shui', 'Sha shui', 'Direction'], 2423,
     '风水上八宫24个方位的砂水吉凶定性-390.md', 16,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-41-feng-shui-in-an-apartment-block.md',
     'reading-feng-shui-in-an-apartment-block',
     'Reading Feng Shui in an Apartment Block',
     '2020-07-05', ['Feng shui', 'City', 'Apartment'], 4208,
     '城市楼房风水断法-476.md', 17,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-42-true-dragon-points-in-city-buildings.md',
     'there-are-true-dragon-points-in-city-buildings',
     'There Are True Dragon Points in City Buildings Too',
     '2017-12-25', ['Feng shui', 'City', 'Dragon point'], 3579,
     '城市楼房中也有“真龙穴”！-637.md', 18,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-43-reading-a-public-cemetery.md',
     'reading-a-public-cemetery',
     'Reading a Public Cemetery',
     '2020-07-11', ['Feng shui', 'Cemetery', 'Graves'], 3771,
     '公墓的风水断法-472.md', 19,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-44-which-floor-is-auspicious.md',
     'which-floor-is-auspicious',
     'Which Floor Is Auspicious?',
     '2020-07-07', ['Feng shui', 'City', 'Apartment'], 1847,
     '哪一个楼层吉利？-475.md', 20,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-45-feng-shui-for-an-only-child.md',
     'feng-shui-for-an-only-child',
     'How Do You Read Feng Shui for an Only Child?',
     '2020-06-04', ['Feng shui', 'Family', 'Bazi'], 1812,
     '独生子女的风水该如何看？-499.md', 21,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-46-feng-shui-during-pregnancy.md',
     'does-feng-shui-during-pregnancy-affect-a-child',
     'Does Feng Shui During Pregnancy Affect a Child’s Fate?',
     '2020-03-10', ['Feng shui', 'Pregnancy', 'Bazi'], 5041,
     '孕期风水对子女的命运有什么影响？-548.md', 22,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-47-do-ancestral-graves-affect-descendants.md',
     'do-ancestral-graves-affect-descendants',
     'Can Ancestral Graves Affect the Fate of Descendants?',
     '2017-11-07', ['Feng shui', 'Graves', 'Family'], 2796,
     '祖坟风水能影响后人的命运吗？-668.md', 23,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-48-ancestral-graves-and-the-family-home.md',
     'ancestral-graves-and-the-family-home',
     'Ancestral Graves and the Family Home',
     '2017-10-30', ['Feng shui', 'Graves', 'Family'], 2901,
     '祖坟和住房风水的关系-675.md', 24,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-49-are-the-ming-tombs-a-good-site.md',
     'are-the-ming-tombs-a-good-feng-shui-site',
     'Are the Ming Tombs a Good Feng Shui Site?',
     '2020-03-06', ['Feng shui', 'Graves', 'Case studies'], 5386,
     '“北京十三陵”是风水宝地吗？-552.md', 25,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),
    ('sample-50-zangshu-of-guo-pu-annotated.md',
     'the-zangshu-of-guo-pu-annotated',
     'The Zangshu of Guo Pu: Text, Meaning and a Scientific Reading',
     '2017-11-23', ['Feng shui', 'Classics', 'Zangshu'], 5406,
     '郭璞《葬经》原文释意和科学解读-652.md', 26,
     'Practising Feng Shui', '风水实务', 'Feng Shui Practice'),

    # 第三批：八字命理。18 篇，order 1-18。
    ('sample-51-logic-of-bazi-reading.md',
     'the-logic-of-bazi-reading',
     'A Brief Word on the Underlying Logic of Bazi Reading',
     '2023-12-09', ['Bazi', 'Fate', 'Method'], 2774,
     '浅谈八字算命的底层逻辑-300.md', 1,
     'Reading the Four Pillars', '八字命理', 'Bazi and Four Pillars'),
    ('sample-52-positioning-system-of-a-bazi.md',
     'the-positioning-system-of-a-bazi',
     'The Positioning System of a Bazi',
     '2020-08-15', ['Bazi', 'Ten gods', 'Method'], 5034,
     '八字的定位系统-455.md', 2,
     'Reading the Four Pillars', '八字命理', 'Bazi and Four Pillars'),
    ('sample-53-time-system-of-bazi.md',
     'the-time-system-of-bazi',
     'The Time System of Bazi Prediction',
     '2020-09-13', ['Bazi', 'Time', 'Method'], 5527,
     '八字预测的时间系统-443.md', 3,
     'Reading the Four Pillars', '八字命理', 'Bazi and Four Pillars'),
    ('sample-54-environmental-system-of-bazi.md',
     'the-environmental-system-of-bazi',
     'The Environmental System of Bazi Prediction',
     '2020-09-16', ['Bazi', 'Feng shui', 'Method'], 2420,
     '八字预测的环境系统-441.md', 4,
     'Reading the Four Pillars', '八字命理', 'Bazi and Four Pillars'),
    ('sample-55-counting-stems-and-branches.md',
     'counting-stems-and-branches',
     'What the Number of Stems and Branches Has to Do With Real Life',
     '2020-08-16', ['Bazi', 'Critique'], 2306,
     '八字干支的数量与现实生活的关系-454.md', 5,
     'Reading the Four Pillars', '八字命理', 'Bazi and Four Pillars'),
    ('sample-56-strength-of-stems-and-branches.md',
     'strength-of-stems-and-branches',
     'How to Judge the Strength and Weakness of Stems and Branches',
     '2020-08-04', ['Bazi', 'Method'], 2529,
     '八字干支旺衰的判断方法-462.md', 6,
     'Reading the Four Pillars', '八字命理', 'Bazi and Four Pillars'),
    ('sample-57-bazi-model-as-functional-mould.md',
     'the-bazi-model-as-a-functional-mould',
     'The “Bazi Model” Really Means a Functional-Feature Model',
     '2018-01-28', ['Bazi', 'Theory'], 3177,
     '八字模型实质是指功能特征模型-628.md', 7,
     'Reading the Four Pillars', '八字命理', 'Bazi and Four Pillars'),
    ('sample-58-bazi-model-worked-example.md',
     'the-bazi-model-in-use',
     'The Bazi Model in Use: A Worked Example',
     '2018-01-26', ['Bazi', 'Case studies'], 2790,
     '“八字模型理论”应用例解-630.md', 8,
     'Reading the Four Pillars', '八字命理', 'Bazi and Four Pillars'),
    ('sample-59-useful-and-unfavourable-gods.md',
     'judging-the-useful-and-unfavourable-gods',
     'The Most Scientific Way to Determine the Useful and Unfavourable Gods',
     '2020-08-02', ['Bazi', 'Method', 'Critique'], 6749,
     '用神和忌神的最科学判断方法-464.md', 9,
     'Reading the Four Pillars', '八字命理', 'Bazi and Four Pillars'),
    ('sample-60-what-the-ten-gods-mean.md',
     'what-the-ten-gods-mean',
     'What the Ten Gods Stand For',
     '2020-02-27', ['Bazi', 'Ten gods'], 5488,
     '八字十神所代表的意义-560.md', 10,
     'Reading the Four Pillars', '八字命理', 'Bazi and Four Pillars'),
    ('sample-61-telling-the-ten-gods-apart.md',
     'telling-the-ten-gods-apart',
     'Telling the Ten Gods Apart in a Person’s Bazi',
     '2021-05-03', ['Bazi', 'Ten gods', 'Method'], 4260,
     '人的八字中十神的鉴别-389.md', 11,
     'Reading the Four Pillars', '八字命理', 'Bazi and Four Pillars'),
    ('sample-62-parallel-versus-rob.md',
     'the-parallel-and-the-rob',
     'The Parallel and the Rob Are Essentially Different',
     '2020-09-26', ['Bazi', 'Ten gods'], 2140,
     '比肩和劫财具有本质上的不同-433.md', 12,
     'Reading the Four Pillars', '八字命理', 'Bazi and Four Pillars'),
    ('sample-63-body-parts-in-the-bazi.md',
     'locating-body-parts-in-a-bazi',
     'Locating Body Parts in a Bazi',
     '2020-08-21', ['Bazi', 'Body', 'Method'], 4342,
     '人体部位在八字中的定位-450.md', 13,
     'Reading the Four Pillars', '八字命理', 'Bazi and Four Pillars'),
    ('sample-64-twins-and-the-exam-score.md',
     'twins-height-and-the-exam-score',
     'Twins, Height and the Exam Score',
     '2020-05-07', ['Bazi', 'Twins', 'Case studies'], 2835,
     '双胞胎高考分数差异的命理秘密-520.md', 14,
     'Reading the Four Pillars', '八字命理', 'Bazi and Four Pillars'),
    ('sample-65-why-identical-bazi-differ.md',
     'why-identical-charts-give-different-fates',
     'Why Do People Born Under the Same Bazi Have Different Fates?',
     '2017-10-16', ['Bazi', 'Theory'], 4457,
     '八字相同为何命运不同？-688.md', 15,
     'Reading the Four Pillars', '八字命理', 'Bazi and Four Pillars'),
    ('sample-66-testing-a-bazi-theory.md',
     'testing-a-bazi-theory',
     'What Is the Only Standard for Testing a Bazi Theory?',
     '2020-04-21', ['Bazi', 'Critique', 'Method'], 3182,
     '检验八字理论正确与否的唯一标准是什么？-533.md', 16,
     'Reading the Four Pillars', '八字命理', 'Bazi and Four Pillars'),
    ('sample-67-feng-shui-and-bazi.md',
     'feng-shui-and-bazi',
     'Feng Shui and Bazi: Which One Decides Fate?',
     '2017-10-19', ['Bazi', 'Feng shui', 'Theory'], 4810,
     '风水和八字的关系-685.md', 17,
     'Reading the Four Pillars', '八字命理', 'Bazi and Four Pillars'),
    ('sample-68-two-reasons-readings-fail.md',
     'two-reasons-a-correct-reading-gets-rejected',
     'Two Reasons a Correct Reading Gets Rejected',
     '2017-11-10', ['Bazi', 'Method', 'Critique'], 4809,
     '解读“算不准命的两个基础原因”-665.md', 18,
     'Reading the Four Pillars', '八字命理', 'Bazi and Four Pillars'),

    # 第四批：预测案例。10 篇（案例集锦一至十），order 1-10。
    ('sample-69-shikongren-method-cases-1.md',
     'the-shikongren-method-cases-1',
     'The Shikongren Method: Cases, Volume One',
     '2026-06-04', ['Divination', 'Method', 'Case studies'], 1650,
     '“吕氏时空人预测术”案例集锦【一】-23.md', 1,
     'Testing the Claim', '预测案例', 'Divination Cases'),
    ('sample-70-shikongren-method-cases-2.md',
     'the-shikongren-method-cases-2',
     'The Shikongren Method: Cases, Volume Two',
     '2026-06-05', ['Divination', 'Method', 'Case studies'], 1858,
     '“吕氏时空人预测术”案例集锦【二】-22.md', 2,
     'Testing the Claim', '预测案例', 'Divination Cases'),
    ('sample-71-shikongren-method-cases-3.md',
     'the-shikongren-method-cases-3',
     'The Shikongren Method: Cases, Volume Three',
     '2026-06-06', ['Divination', 'Method', 'Case studies'], 306,
     '“吕氏时空人预测术”案例集锦【三】-21.md', 3,
     'Testing the Claim', '预测案例', 'Divination Cases'),
    ('sample-72-shikongren-method-cases-4.md',
     'the-shikongren-method-cases-4',
     'The Shikongren Method: Cases, Volume Four',
     '2026-06-07', ['Divination', 'Method', 'Case studies'], 515,
     '“吕氏时空人预测术”案例集锦【四】-20.md', 4,
     'Testing the Claim', '预测案例', 'Divination Cases'),
    ('sample-73-shikongren-method-cases-5.md',
     'the-shikongren-method-cases-5',
     'The Shikongren Method: Cases, Volume Five',
     '2026-06-08', ['Divination', 'Method', 'Case studies'], 1334,
     '“吕氏时空人预测术”案例集锦【五】-19.md', 5,
     'Testing the Claim', '预测案例', 'Divination Cases'),
    ('sample-74-shikongren-method-cases-6.md',
     'the-shikongren-method-cases-6',
     'The Shikongren Method: Cases, Volume Six',
     '2026-06-09', ['Divination', 'Method', 'Case studies'], 1187,
     '“吕氏时空人预测术”案例集锦【六】-18.md', 6,
     'Testing the Claim', '预测案例', 'Divination Cases'),
    ('sample-75-shikongren-method-cases-7.md',
     'the-shikongren-method-cases-7',
     'The Shikongren Method: Cases, Volume Seven',
     '2026-06-10', ['Divination', 'Method', 'Case studies'], 1222,
     '“吕氏时空人预测术”案例集锦【七】-17.md', 7,
     'Testing the Claim', '预测案例', 'Divination Cases'),
    ('sample-76-mount-wudang-time-space-person.md',
     'mount-wudang-a-discussion-of-time-space-and-person',
     'Mount Wudang: A Discussion of Time, Space and Person',
     '2026-06-11', ['Divination', 'Theory', 'Case studies'], 5557,
     '“吕氏时空人预测术”案例集锦【八】-16.md', 8,
     'Testing the Claim', '预测案例', 'Divination Cases'),
    ('sample-77-shikongren-method-cases-9.md',
     'the-shikongren-method-cases-9',
     'The Shikongren Method: Cases, Volume Nine',
     '2026-07-20', ['Divination', 'Method', 'Case studies'], 3823,
     '“吕氏时空人预测术”案例集锦【九】-4.md', 9,
     'Testing the Claim', '预测案例', 'Divination Cases'),
    ('sample-78-shikongren-method-cases-10.md',
     'the-shikongren-method-cases-10',
     'The Shikongren Method: Cases, Volume Ten',
     '2026-07-21', ['Divination', 'Method', 'Case studies'], 4238,
     '“吕氏时空人预测术”案例集锦【十】-3.md', 10,
     'Testing the Claim', '预测案例', 'Divination Cases'),
]


def body_of(text):
    """去掉 H1、署名行、以及末尾的译者页脚，只留正文。

    注意：开头的译者注（引用块）**必须保留** —— 读者在文章页应该看到它，
    只是 build.py 的 clean_excerpt 会把它从摘要里剥掉。所以这里不能顺手
    把引用块一起吞了。H1 则要删：站点标题已经在 frontmatter 里，
    正文再出现一次会让首页摘要以标题开头。
    """
    lines = text.split('\n')
    # 删掉开头 12 行内的第一处 H1（可能在译者注和分隔线之后）
    for k in range(min(len(lines), 12)):
        if lines[k].startswith('# '):
            del lines[k]
            break
    out = []
    i = 0
    # 跳过开头空行与 H1
    while i < len(lines) and (not lines[i].strip() or lines[i].startswith('# ')):
        i += 1
    # 跳过署名行（*By ... *）
    while i < len(lines) and (not lines[i].strip() or
                              (lines[i].startswith('*By ') and lines[i].rstrip().endswith('*'))):
        i += 1
    rest = lines[i:]
    # 去掉尾部 "---\n\n*Translated from ...*"
    cut = len(rest)
    for j in range(len(rest) - 1, -1, -1):
        if rest[j].strip().startswith('*Translated from the Chinese'):
            cut = j
            # 再往上吃掉分隔线
            k = j - 1
            while k >= 0 and not rest[k].strip():
                k -= 1
            if k >= 0 and rest[k].strip() == '---':
                cut = k
            break
    rest = rest[:cut]
    return '\n'.join(rest).strip('\n')


def rows():
    """第一批用模块级 SERIES/CAT_*，第二批每行自带 11 个字段。"""
    for e in MAP:
        yield e + (SERIES, CAT_KEY, CAT_EN)
    for e in MAP2:
        yield e


def main():
    os.makedirs(DST, exist_ok=True)
    made = 0
    for fn, slug, title, date, tags, zh_words, src, order, series, cat_key, cat_en in rows():
        p = os.path.join(SRC, fn)
        if not os.path.exists(p):
            print('  ! 缺文件：%s' % fn)
            continue
        with open(p, encoding='utf-8') as f:
            raw = f.read()
        body = body_of(raw)
        if not body:
            print('  ! 正文为空：%s' % fn)
            continue
        # 站点展示的是「这篇英文多少词」，不是中文原文多少字 ——
        # zh_words 是中文原文字数，只作对照记录，不进 frontmatter。
        en_words = len(body.split())
        tag_s = ', '.join('"%s"' % t for t in tags)
        fm = (
            '---\n'
            'title: "%s"\n'
            'date: "%s"\n'
            'category: "%s"\n'
            'cat_key: "%s"\n'
            'tags: [%s]\n'
            'author: "Lü Wenyi"\n'
            'series: "%s"\n'
            'order: %d\n'
            'src: "%s"\n'
            'words: %d\n'
            '---\n\n'
        ) % (title.replace('"', '\\"'), date, cat_en, cat_key, tag_s,
             series, order, src, en_words)
        out = os.path.join(DST, slug + '.md')
        with open(out, 'w', encoding='utf-8', newline='\n') as f:
            f.write(fm + body + '\n')
        print('  + %s  %d words (中文 %d 字)' % (slug + '.md', en_words, zh_words))
        made += 1
    print('共 %d 篇' % made)


if __name__ == '__main__':
    main()
