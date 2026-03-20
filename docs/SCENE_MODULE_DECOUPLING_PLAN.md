# 场景搭建模块解耦设计方案

版本：v0.1  
更新时间：2026-03-19  
状态：立即生效的架构约束

## 1. 文档目的

这份文档用于明确：

1. 动作分析模块
2. 场景搭建模块

从现在开始必须按“独立模块”建设，而不是继续产品、代码、数据库、图谱层高度耦合。

这不是可选优化，而是当前开发约束。

## 2. 核心结论

场景搭建模块必须与动作分析模块解耦。

解耦范围包括：

1. 用户端入口
2. 运营端入口
3. 后端服务
4. 数据表
5. Neo4j 节点与边
6. 运营维护页面

## 3. 为什么必须解耦

原因：

1. 动作分析后台已经足够复杂
2. 场景搭建未来也会发展成完整体系
3. 如果继续堆进 `admin`，维护成本会快速失控
4. 场景与动作虽然有关联，但不是同一类对象
5. 未来第三模块“导演思维融合”应作为上层聚合，而不是让底层两个模块缠在一起

## 4. 产品层解耦

### 用户端

建议明确并列：

1. 动作分析
2. 场景搭建

当前不建议：

1. 把场景搭建塞进动作图谱推荐页
2. 把动作分析和场景搭建混成一个结果区

### 运营端

建议明确分成两个后台入口：

1. `admin.html`
   - 动作分析后台
2. `scene.html`
   - 场景搭建后台

说明：

1. `admin` 继续服务动作图谱、动作补充单、动作运营
2. `scene` 未来服务场景图谱、场景补充单、场景运营

## 5. 代码层解耦

当前和后续都建议维持：

### 动作模块

1. `action_verbs.py`
2. `action_sfx_graph.py`
3. `action_graph_manage.py`
4. `action_graph_neo4j.py`

### 场景模块

1. `scene_building.py`
2. `scene_sfx_graph.py`
3. 后续新增：
   - `scene_graph_manage.py`
   - `scene_graph_neo4j.py`

要求：

1. 不要把 scene 逻辑继续混写进 action 服务文件
2. 不要把 scene 的提示词、图谱结构、运营逻辑塞进 action 模块

## 6. 数据库层解耦

当前动作模块已有：

1. `action_supplement_task`
2. `action_supplement_asset`

场景模块建议独立新增：

1. `scene_analysis`
2. `scene_supplement_task`
3. `scene_supplement_asset`
4. 后续若有运营复核池，也应单独建 scene 侧表

原则：

1. 不与 action 复用主业务表
2. 允许未来在上层做聚合
3. 不允许把 scene 数据硬塞进 action 表

## 7. Neo4j 层解耦

动作图谱已有：

1. 父节点
2. 语义扩展词
3. 直达音效
4. 组合音效

场景搭建应独立建设：

1. 场景主节点
2. 场景构成词
3. 直达场景音效
4. 组合场景音效
5. 场景模板 / 场景集合

要求：

1. 关系类型单独命名
2. 不与动作图谱边混用
3. 查询入口单独维护

## 8. 两个模块会不会完全没有关联

不会。

正确关系是：

1. 底层独立
2. 上层有限联动

当前阶段：

1. 动作分析独立跑
2. 场景搭建独立跑

未来阶段：

1. 结果页做联动提示
2. 第三模块“导演思维融合”统一读取两个模块的结果

所以：

1. 两个模块不能底层耦死
2. 但未来会在上层被同时读取和整合

## 9. 第三模块的关系

未来第三模块建议定位为：

1. 动作分析结果
2. 场景搭建结果
3. 情绪音乐 / 创作思路

的上层融合模块。

这意味着：

1. 第三模块依赖动作模块
2. 第三模块依赖场景模块
3. 但动作与场景不应为了第三模块而提前耦合

## 10. 当前执行原则

从当前开始，开发上执行以下约束：

1. 场景模块新功能优先落到 `scene_*`
2. 运营端优先新增 `scene.html`，不再继续堆到 `admin.html`
3. 场景业务数据单独建表
4. 场景图谱单独建 Neo4j 节点和关系
5. 只有上层汇总页才允许同时引用动作和场景结果

## 11. 当前落地状态

目前已开始独立的部分：

1. `/Users/demo/Documents/New project/musicForArt/backend/app/services/scene_building.py`
2. `/Users/demo/Documents/New project/musicForArt/backend/app/services/scene_sfx_graph.py`
3. `/Users/demo/Documents/New project/musicForArt/docs/SCENE_BUILDING_MODULE_STRATEGY.md`
4. `/Users/demo/Documents/New project/musicForArt/docs/SCENE_BUILDING_OUTPUT_SCHEMA.md`
5. `/Users/demo/Documents/New project/musicForArt/docs/SCENE_BUILDING_PROMPT_V1.md`

当前待继续推进：

1. `scene.html` 后台壳子
2. scene 侧独立数据库表
3. scene 侧 Neo4j 图谱设计
4. 用户端场景搭建入口

## 12. 结论

场景搭建不是动作分析后台里的一个功能区，而是一个独立模块。

后续开发必须按照“模块独立、上层联动”的原则推进。
