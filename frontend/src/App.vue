<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">日志采集与事件追踪平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">面向应用日志收集、事件埋点追踪、流式采集与结构化归集的多源日志采集平台。</span>
        <span class="head-user">当前值班：数据工程组</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
const navItems = [{ label: "运营概览", path: "/" }, { label: "日志源管理", path: "/log_source" }, { label: "日志解析", path: "/log_parser" }, { label: "事件埋点", path: "/event_tracker" }, { label: "流式缓冲", path: "/stream_buffer" }, { label: "字段映射", path: "/field_mapping" }, { label: "采集代理", path: "/agent_heartbeat" }, { label: "事件校验", path: "/event_validate" }, { label: "日志轮转", path: "/log_rotate" }, { label: "日志富化", path: "/enrich_join" }, { label: "告警规则", path: "/alert_rule" }, { label: "日志分区", path: "/log_partition" }, { label: "采集位点", path: "/collect_checkpoint" }, { label: "窗口去重", path: "/dedup_window" }, { label: "聚合计算", path: "/aggregate_olap" }, { label: "格式输出", path: "/format_output" }, { label: "回溯采集", path: "/backfill_range" }, { label: "采样策略", path: "/sampling_rule" }, { label: "质量诊断", path: "/log_quality" }]
</script>
