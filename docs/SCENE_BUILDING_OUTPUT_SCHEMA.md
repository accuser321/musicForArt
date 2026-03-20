# 场景搭建模块输出结构定义

版本：v0.1  
更新时间：2026-03-19  
状态：可作为 Prompt、后端接口、Neo4j 对接、前端展示的统一结构草案

## 1. 文档目的

这份文档用于定义“场景搭建模块”第一版的结构化输出格式。

目标：

1. 让 LLM 输出有固定字段
2. 让后端知道应该如何接收和存储
3. 让 Neo4j / 图谱命中有稳定入口
4. 让前端展示有明确数据来源

说明：

这份结构不是最终不可变协议，但当前应作为第一版实现基线。

## 2. 设计原则

### 2.1 当前阶段只做文本场景分析

当前结构只服务以下目标：

1. 识别文本中的时间与地点
2. 识别场景切换点
3. 拆解场景构成元素
4. 输出场景相关音效候选
5. 区分场景音效与人物动作音效

当前不做：

1. 导演思维分析
2. 镜头语言分析
3. 混响、延迟、远近景的创作建议

### 2.2 场景搭建强调“可复制的大场景集合”

所以结构中必须支持：

1. 当前文本场景结论
2. 与系统已有大场景集合的映射
3. 场景构成词
4. 直达场景音效
5. 组合场景音效

## 3. 顶层结构

建议第一版输出为：

```json
{
  "title": "场景搭建分析",
  "genre": "玄幻",
  "scene_items": [],
  "summary": {},
  "graph_model": {}
}
```

## 4. 顶层字段定义

### 4.1 `title`

类型：

```json
"string"
```

固定建议值：

```json
"场景搭建分析"
```

### 4.2 `genre`

类型：

```json
"string"
```

含义：

当前分析所使用的赛道。

例如：

```json
"玄幻"
```

### 4.3 `scene_items`

类型：

```json
[
  {
    "...": "..."
  }
]
```

含义：

按“场景切换片段”拆出的场景分析结果列表。

说明：

1. 不要求按句子一一拆分
2. 应按“场景是否发生变化”来拆
3. 同一场景连续出现时，不应重复拆成多个场景项

### 4.4 `summary`

类型：

```json
{
  "scene_count": 0,
  "asset_count": 0,
  "gap_count": 0
}
```

含义：

用于前端概览展示。

### 4.5 `graph_model`

类型：

```json
{
  "parent": "赛道+场景主节点",
  "children": [
    "time_terms",
    "location_terms",
    "scene_elements",
    "scene_sfx_terms",
    "missing_scene_sfx_terms"
  ],
  "node_example": "玄幻::山林夜路"
}
```

含义：

说明场景图谱的节点组织方式。

## 5. `scene_items` 单项结构

单个场景项建议结构如下：

```json
{
  "scene_id": "scene_001",
  "scene_name": "山林夜路",
  "node_key": "玄幻::山林夜路",
  "sentence_excerpt": "夜风卷过林间小路，树影摇晃。",
  "is_scene_change": true,
  "scene_change_reason": "地点由室内转为山林夜路",
  "time_terms": [],
  "location_terms": [],
  "time_inference": [],
  "location_inference": [],
  "background_elements": [],
  "feature_elements": [],
  "detail_elements": [],
  "supporting_sfx_terms": [],
  "detail_sfx_terms": [],
  "excluded_action_terms": [],
  "scene_graph_source": {},
  "assets": [],
  "missing_scene_sfx_terms": []
}
```

## 6. 字段逐项定义

### 6.1 `scene_id`

类型：

```json
"string"
```

含义：

单次分析中的场景项唯一标识。

例如：

```json
"scene_001"
```

### 6.2 `scene_name`

类型：

```json
"string"
```

含义：

当前场景项的主场景名称。

例如：

```json
"山林夜路"
```

### 6.3 `node_key`

类型：

```json
"string"
```

含义：

图谱节点 key。

例如：

```json
"玄幻::山林夜路"
```

### 6.4 `sentence_excerpt`

类型：

```json
"string"
```

含义：

触发当前场景识别的原句片段。

### 6.5 `is_scene_change`

类型：

```json
true
```

含义：

是否构成新的场景切换点。

规则：

1. 同一场景连续出现，不重复标记
2. 只有场景发生变化时，才为 `true`

### 6.6 `scene_change_reason`

类型：

```json
"string"
```

含义：

为什么这里被识别为新的场景切换点。

例如：

1. 地点变化
2. 时间变化
3. 时空共同变化

### 6.7 `time_terms`

类型：

```json
["晚上", "深夜"]
```

含义：

文本中直接出现的时间词。

### 6.8 `location_terms`

类型：

```json
["山林", "小路"]
```

含义：

文本中直接出现的地点词。

### 6.9 `time_inference`

类型：

```json
["夜间环境", "可考虑虫鸣或夜风类环境声"]
```

含义：

由时间词推导出的场景范围或典型环境提示。

### 6.10 `location_inference`

类型：

```json
["室外", "开阔", "自然环境"]
```

含义：

由地点词推导出的场景属性。

### 6.11 `background_elements`

类型：

```json
["夜风", "空气流动", "远处虫鸣"]
```

含义：

背景元素。

规则：

1. 尽量是通用内容
2. 用于打底
3. 不要求特别强的辨识度

### 6.12 `feature_elements`

类型：

```json
["树叶响动", "海浪", "粉笔声"]
```

含义：

特征元素。

规则：

1. 能帮助判断场景
2. 是场景的标志性声音

### 6.13 `detail_elements`

类型：

```json
["远处海鸥", "零星脚步", "人群稀疏喧闹"]
```

含义：

细节元素。

规则：

1. 让场景更丰满
2. 让场景更灵动
3. 不是每次都必须加

### 6.14 `supporting_sfx_terms`

类型：

```json
["海风", "海浪", "海鸥"]
```

含义：

支撑场景成立的核心音效词。

规则：

1. 优先用于搭建场景
2. 如果这些已经足够，不必再强行加细节层音效

### 6.15 `detail_sfx_terms`

类型：

```json
["孩子嬉闹声", "远处轮船声", "汽笛声"]
```

含义：

用于丰富层次的补充音效词。

规则：

1. 只有在支撑音效不够时，再补充这部分
2. 不应无限扩张，避免场景过满

### 6.16 `excluded_action_terms`

类型：

```json
["拍手", "跺脚"]
```

含义：

这些词虽然在文本里出现，但属于人物动作音效，不应作为场景搭建模块的环境音效处理。

这用于明确场景模块与动作分析模块的边界。

### 6.17 `scene_graph_source`

类型：

```json
{
  "common_hit": true,
  "genre_hit": false,
  "template_hit": "夜林类",
  "has_fallback_terms": false
}
```

含义：

表示场景图谱来源。

建议字段：

1. `common_hit`
2. `genre_hit`
3. `template_hit`
4. `has_fallback_terms`

### 6.18 `assets`

类型：

```json
[
  {
    "label": "海浪",
    "file_name": "海浪.wav",
    "display_name": "海浪（通用）",
    "score": 0.92,
    "download_api": "/api/sfx/file?path=..."
  }
]
```

含义：

当前场景项已命中的可下载素材。

### 6.19 `missing_scene_sfx_terms`

类型：

```json
["海鸥", "远处轮船声"]
```

含义：

当前场景项识别出来但尚未命中素材的场景音效词。

## 7. `summary` 结构定义

建议如下：

```json
{
  "scene_count": 3,
  "asset_count": 12,
  "gap_count": 4
}
```

### 7.1 `scene_count`

表示本次分析一共拆出了多少个场景项。

### 7.2 `asset_count`

表示当前所有场景项一共命中了多少条素材候选。

### 7.3 `gap_count`

表示当前所有场景项里，一共还有多少个场景音效词缺少素材覆盖。

## 8. 示例 JSON

```json
{
  "title": "场景搭建分析",
  "genre": "玄幻",
  "graph_model": {
    "parent": "赛道+场景主节点",
    "children": [
      "time_terms",
      "location_terms",
      "scene_elements",
      "scene_sfx_terms",
      "missing_scene_sfx_terms"
    ],
    "node_example": "玄幻::山林夜路"
  },
  "summary": {
    "scene_count": 1,
    "asset_count": 3,
    "gap_count": 2
  },
  "scene_items": [
    {
      "scene_id": "scene_001",
      "scene_name": "山林夜路",
      "node_key": "玄幻::山林夜路",
      "sentence_excerpt": "夜风卷过林间小路，树影在月色里晃动。",
      "is_scene_change": true,
      "scene_change_reason": "地点和时间共同限定为山林夜路场景",
      "time_terms": ["夜", "月色"],
      "location_terms": ["林间小路"],
      "time_inference": ["夜间环境"],
      "location_inference": ["室外", "自然环境"],
      "background_elements": ["夜风", "空气流动"],
      "feature_elements": ["树叶响动", "远处虫鸣"],
      "detail_elements": ["偶发枯枝轻响"],
      "supporting_sfx_terms": ["夜风", "树叶声", "虫鸣"],
      "detail_sfx_terms": ["枯枝轻响"],
      "excluded_action_terms": [],
      "scene_graph_source": {
        "common_hit": true,
        "genre_hit": true,
        "template_hit": "夜林类",
        "has_fallback_terms": false
      },
      "assets": [
        {
          "label": "夜风",
          "file_name": "night_wind.wav",
          "display_name": "夜风（通用）",
          "score": 0.95,
          "download_api": "/api/sfx/file?path=night_wind.wav"
        }
      ],
      "missing_scene_sfx_terms": ["树叶声", "虫鸣"]
    }
  ]
}
```

## 9. 与 Neo4j 的对接建议

第一版结构建议映射为：

1. `scene_name / node_key`
   - 场景主节点
2. `background_elements / feature_elements / detail_elements`
   - 场景构成词边
3. `supporting_sfx_terms`
   - 场景支撑音效边
4. `detail_sfx_terms`
   - 场景细节音效边
5. `scene_graph_source.template_hit`
   - 场景模板 / 场景集合节点

## 10. 与 Prompt 的关系

后续提示词应围绕这份输出结构生成。

也就是说：

1. Prompt 不能只要求模型“写分析”
2. Prompt 必须要求模型按这份结构稳定输出
3. 字段缺失时要输出空数组，而不是省略关键字段

## 11. 当前结论

这份结构已经足够支持下一步工作：

1. 继续从老师逐字稿提炼正式 Prompt
2. 开始设计场景图谱与 Neo4j 节点
3. 开始设计用户端第一版展示

后续可以继续迭代，但当前不建议在没有结构基线的情况下直接写场景模块代码。
