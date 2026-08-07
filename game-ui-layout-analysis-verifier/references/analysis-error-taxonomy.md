# A2 布局分析错误分类

以下稳定 `error_type` 只描述 engine-neutral 的布局分析错误。严重程度是常见默认值，应根据对页面骨架、证据可信度和下游可用性的实际影响调整。

| 错误类型 | 定义 | 常见表现 | 示例 | 推荐 correction action | 通常严重程度 |
| --- | --- | --- | --- | --- | --- |
| `missing_region` | 截图中的重要一级区域未记录 | 漏掉顶部状态、主内容、详情或操作区 | 商城顶部资源条完全缺失 | `added` | `major` |
| `extra_region` | 分析中存在截图不支持的区域 | 把装饰光效当成功能区 | 为背景光斑创建操作区 | `removed` | `major` |
| `wrong_page_type` | 页面类型与主要用途不符 | 商城被标为背包 | 有价格和购买语境却标为 `inventory` | `modified` | `major` |
| `wrong_page_purpose` | 页面主要目标描述错误或过度扩展 | 把浏览描述成匹配流程 | 无对局证据却声称页面用于匹配 | `modified` | `major` |
| `wrong_page_state` | 当前可见状态判断错误 | 把默认态写成购买成功态 | 仅有选中卡却声称交易完成 | `modified` | `minor` |
| `wrong_presentation_mode` | 全屏、弹窗、HUD 或混合形态判断错误 | 覆盖弹窗被当全屏页 | 可见遮罩和底层页面却标为 `fullscreen` | `modified` | `major` |
| `wrong_region_type` | region 功能类型不正确 | 详情区被标为导航区 | 属性面板标成 `navigation_region` | `modified` | `minor` |
| `wrong_region_boundary` | 粗略区域边界明显偏离可见结构 | 边界漏掉主体或吞并相邻区 | 列表边界覆盖整个详情区 | `modified` | `minor` |
| `wrong_region_parent` | 父子层级与视觉包含关系不符 | 子区挂到无关区域 | 详情内操作区挂到导航区 | `modified` | `major` |
| `wrong_region_relationship` | 关系类型、方向或对象错误 | 相邻被写成控制 | 两个并列面板被断言互相更新 | `modified` | `major` |
| `over_split_region` | 一个功能区域被机械拆成过多 region | 每个边框都成为一级区 | 一个按钮的底板和文字分别成为区域 | `removed` 或 `modified` | `minor` |
| `over_merged_region` | 多个独立职责被错误合并 | 列表与详情共用一个模糊区 | 无法表达选择与详情对应 | `removed` 后 `added` | `major` |
| `missing_component_group` | 区域内的重要逻辑组未记录 | 漏掉卡片列表或操作组 | 商品区只有 region，没有商品卡 group | `added` | `minor` |
| `extra_component_group` | 截图不支持该逻辑组 | 装饰纹样被建成按钮组 | 背景星点被当奖励槽 | `removed` | `minor` |
| `wrong_component_group` | 组件组类型、归属或粒度错误 | 卡片列表标成文本信息组 | 重复商品卡未表达为列表 | `modified` | `minor` |
| `wrong_repeat_count` | 可见数量错误或猜测不可见总量 | 六张卡写成八张 | 裁切列表被写入完整总数 | `modified` 或 `downgraded_to_uncertain` | `minor` |
| `decoration_as_control` | 装饰内容被误判为交互控件 | 发光边框被当按钮 | 背景角色被标成可选入口 | `removed` | `major` |
| `control_as_decoration` | 明显控件被当作纯装饰 | 主按钮被忽略 | 高亮操作组被放入背景内容 | `modified` 或 `added` | `major` |
| `wrong_visual_hierarchy` | 主次焦点或支持信息排序错误 | 最大面积等同最重要 | 低对比背景被设为主焦点 | `modified` | `major` |
| `wrong_primary_action` | 主要操作对象判断错误 | 返回键被当购买按钮 | 次级关闭入口被标为主操作 | `modified` 或 `downgraded_to_uncertain` | `major` |
| `unsupported_inference` | 功能或交互描述没有截图证据 | 虚构点击结果、消费或跳转 | 声称点击后消耗资源并进入匹配 | `removed` 或 `downgraded_to_uncertain` | `major` |
| `evidence_level_mismatch` | `observed`、`inferred`、`uncertain` 使用错误 | 推断被标为直接观察 | 分类控制列表被标为 `observed` | `modified` | `major` |
| `confidence_mismatch` | 置信度与证据强度不匹配 | 单一线索却给出 0.99 | 推断的主操作置信度过高 | `modified` | `minor` |
| `input_metadata_unverified` | 图片尺寸、方向、文件名或来源缺少可信执行环境证据 | 视觉模型估算像素尺寸或元数据未核实 | draft 声称 1920×1080，但运行环境未提供图片元数据 | `unresolved` 或 `modified` | `minor` |
| `brand_content_leak` | 输出建议继承具体品牌或美术内容 | 推荐复制图标、角色或品牌配色 | layout rule 要求复用货币图标造型 | `modified` 或 `removed` | `major` |
| `terminology_inconsistency` | 同一概念使用多套字段或术语 | page type 混用不同命名 | 同时使用 `pageType` 与 `page_type` | `modified` | `minor` |
| `broken_reference` | 结构化 ID 指向不存在或已删除对象 | final 残留旧 region 引用 | layout rule 引用被删除 group | `modified` 或 `removed` | `critical` |
| `other` | 无法归入稳定分类的分析问题 | 罕见但有明确证据的问题 | 在 finding 中说明为何无法分类 | 依实际情况 | 依实际情况 |

不得添加引擎节点、Prefab、九宫格或切图错误类型；这些内容不属于 A2 职责。
