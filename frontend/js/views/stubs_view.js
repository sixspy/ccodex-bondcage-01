export const stubsViewTemplate = `
<section class="panel">
  <h2>增补 / 拆笼</h2>
  <div class="stub-banner">
    增补已按《增补扩笼纪要》(ADR-2026-11-22-TOPUP-01) 锁死为<strong>甲路径：同笼扩额整笔落库</strong>——
    一笔事务内抬高笼总额/可用、推进笼代、同步增加当事人冻结；任一步失败整笔回滚。
    影子桥接（新笼桥接）为扩笼偏航，只记审计并拒绝。拆笼仍为空桩。
  </div>
  <div class="grid-2">
    <div>
      <h3>增补入笼（真扩额）</h3>
      <div class="form-row">
        <div>
          <label>笼</label>
          <select v-model.number="topupForm.cage_id">
            <option :value="0">请选择</option>
            <option v-for="c in cages" :key="'t'+c.id" :value="c.id">
              {{ c.cage_code }} · 可用 {{ fmt(c.available) }} · 代{{ c.generation }}
            </option>
          </select>
        </div>
        <div>
          <label>增补金额（从当事人可用冻入）</label>
          <input type="number" v-model.number="topupForm.amount" min="1"/>
        </div>
        <div>
          <label>口径（锁死）</label>
          <select v-model="topupForm.mode">
            <option>同笼扩代</option>
          </select>
        </div>
      </div>
      <div class="actions">
        <button class="ok" @click="doTopupExpand">同笼扩额增补</button>
        <button class="secondary" @click="refresh">刷新</button>
      </div>
      <p class="hint">
        成功后：笼总额与可用同抬、笼代 +1；账户冻结同抬、余额不变；对账互证保持真绿。
        两人同补同笼时后到者只留争用痕，不写业务。
      </p>
      <table>
        <thead><tr><th>ID</th><th>笼</th><th>金额</th><th>口径</th><th>状态</th><th>明细</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="j in topupJobs" :key="j.id">
            <td>{{ j.id }}</td>
            <td>{{ j.cage_code }}</td>
            <td>{{ fmt(j.amount) }}</td>
            <td>{{ j.mode }}</td>
            <td><span class="badge pass">{{ j.status }}</span></td>
            <td style="font-size:11px">{{ j.detail }}</td>
            <td>{{ j.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div>
      <h3>拆笼分拨（空桩）</h3>
      <div class="form-row">
        <div>
          <label>笼</label>
          <select v-model.number="splitForm.cage_id">
            <option :value="0">请选择</option>
            <option v-for="c in cages" :key="'s'+c.id" :value="c.id">
              {{ c.cage_code }} · 可用 {{ fmt(c.available) }}
            </option>
          </select>
        </div>
        <div>
          <label>左金额</label>
          <input type="number" v-model.number="splitForm.left_amount" min="1"/>
        </div>
        <div>
          <label>右金额</label>
          <input type="number" v-model.number="splitForm.right_amount" min="1"/>
        </div>
        <div>
          <label>模式</label>
          <select v-model="splitForm.mode">
            <option>整笔切分</option>
            <option>影子子笼</option>
          </select>
        </div>
      </div>
      <div class="actions">
        <button @click="doSplitStub">登记拆笼空桩</button>
      </div>
      <table>
        <thead><tr><th>ID</th><th>笼</th><th>左</th><th>右</th><th>模式</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="j in splitJobs" :key="j.id">
            <td>{{ j.id }}</td>
            <td>{{ j.cage_code }}</td>
            <td>{{ fmt(j.left_amount) }}</td>
            <td>{{ fmt(j.right_amount) }}</td>
            <td>{{ j.mode }}</td>
            <td>{{ j.status }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</section>
`;
