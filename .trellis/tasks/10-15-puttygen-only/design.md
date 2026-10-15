# 技术设计

根目录使用单产品结构：src/{core,app,ui}、resources、tests/{core,gui}、packaging、cmake、tools/qt、docs 和 vendor。根 CMake 直接配置 PuTTYgen，保留 PG_BUILD_GUI 和现有密钥目标名称，移除 PUTTY_BUILD_* 与 Suite*.cmake。保持 GUI 和密码业务行为不变，不增加新功能。

整体搬移 apps/puttygen 的资源和测试，逐一更新资源许可证相对路径、Python 脚本根定位、PKGBUILD、HEAD 源码白名单、部署和 CI 路径。官方第三方完整源树不是独立交付组件，保留其构建及参考 puttygen CLI；审计并反向应用仅为已删除套件引入的补丁 0003—0011，保留密钥核心所需补丁。若补丁仍为 PuTTYgen 所需，记录证据再保留。

删除 apps/{putty,pageant,plink,pscp,psftp}、libs/putty-runtime、tools/suite、套件专用工作流与规范。原套件任务标记为范围变更取消，保留历史记录。AI 基础设施和项目 .tmp 历史不批量删除。目录迁移以 fresh build 和跨层路径审计证明行为保持。
