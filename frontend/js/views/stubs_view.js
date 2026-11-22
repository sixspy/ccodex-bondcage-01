export const stubsViewTemplate = `
<section class="panel">
  <h2>增补入笼 / 拆笼空桩</h2>
  <div class="stub-banner" v-if="topupFate">
    <strong>增补扩笼命运说明 · {{ topupFate.fate_date }} · 已锁死</strong>
    <div>路径 {{ topupFate.path }} · 模式「{{ topupFate.mode }}」</div>
    <div>{{ topupFate.note }}</div>
  </div>
  <div class="stub-banner">
    拆笼现状：split_svc 仍为空桩。只登记 job 与审计，不改笼额、不生子笼。
    题面落地时须择一写路径（整笔切分 vs 影子子笼）。
  </div>
  <div class="grid-2">
    <div>
      <h3>增补入笼（甲路径 · 同笼就地扩额）</h3>
      <p class="hint">
        整笔改写原笼行总额与可用、抬账户冻结、升代并记审计；笼主键与对外笼号不变。
        任一步失败整笔回滚。模式固定为「{{ topupFate ? topupFate.mode : '同笼扩代' }}」，
        选其他模式将被记「扩笼偏航」并拒绝写入。
      </p>
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
          <label>金额</label>
          <input type="number" v-model.number="topupForm.amount" min="1"/>
        </div>
        <div>
          <label>模式（按命运说明）</label>
          <select v-model="topupForm.mode">
            <option>{{ topupFate ? topupFate.mode : '同笼扩代' }}</option>
          </select>
        </div>
      </div>
      <div class="actions">
        <button @click="doTopupStub">增补扩额</button>
      </div>
      <table>
        <thead>
          <tr><th>ID</th><th>笼</th><th>金额</th><th>模式</th><th>代</th><th>正式笼号</th><th>状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="j in topupJobs" :key="j.id">
            <td>{{ j.id }}</td>
            <td>{{ j.cage_code }}</td>
            <td>{{ fmt(j.amount) }}</td>
            <td>{{ j.mode }}</td>
            <td>{{ j.generation_before }}→{{ j.generation_after }}</td>
            <td>{{ j.formal_cage_code }}</td>
            <td>{{ j.status }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div>
      <h3>拆笼分拨</h3>
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
