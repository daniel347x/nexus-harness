const {test} = require('node:test');
const assert = require('node:assert/strict');
const {verifyChoices} = require('./run_manager_choices_live.cjs');

function snapshot(phone) {
  const ids = phone ? ['m-new-runtime','m-new-agent','m-new-oc-model','m-new-oc-variant','m-new-effort'] :
    ['manager-new-runtime','opencode-new-agent','opencode-new-model','opencode-new-variant','claude-new-effort'];
  const select = (id, values, value, visible=true) => ({id,value,visible,options:values.map(v=>({value:v,text:v}))});
  const model = select(ids[2],[phone?'35':'openai/gpt-6.1-sol-fast'],phone?'35':'openai/gpt-6.1-sol-fast');
  model.options[0].text = phone ? 'GPT-6.1 Sol Fast · openai' : 'openai/gpt-6.1-sol-fast · GPT-6.1 Sol Fast';
  model.options.push({value:'other',text:'Another model'});
  return {claudeModel:'claude-opus-5-5',selects:[select(ids[0],['claude','opencode'],'opencode'),
    select(ids[1],['manager','manager-gpt'],'manager-gpt'),model,
    select(ids[3],['','low','medium','high','xhigh'],'xhigh'),select(ids[4],['high','xhigh','max'],'xhigh',false)]};
}

for (const phone of [false,true]) {
  test((phone?'phone':'desktop')+' live gate rejects empty, hidden, placeholder or wrongly wired choices', () => {
    verifyChoices(snapshot(phone),{phone,runtime:'opencode',selected:true});
    for (let index=0;index<4;index++) {
      const empty=snapshot(phone); empty.selects[index].options=[];
      assert.throws(()=>verifyChoices(empty,{phone,runtime:'opencode',selected:true}));
      const hidden=snapshot(phone); hidden.selects[index].visible=false;
      assert.throws(()=>verifyChoices(hidden,{phone,runtime:'opencode',selected:true}));
    }
    const wrong=snapshot(phone);wrong.selects[3].value='high';
    assert.throws(()=>verifyChoices(wrong,{phone,runtime:'opencode',selected:true}));
    const loading=snapshot(phone);loading.selects[2].options=[{value:'',text:'Reading the catalog…'}];
    assert.throws(()=>verifyChoices(loading,{phone,runtime:'opencode',selected:true}));
  });
  test((phone?'phone':'desktop')+' live gate also requires populated visible native Claude controls', () => {
    const s=snapshot(phone);s.selects[0].value='claude';s.selects[4].visible=true;
    for (const i of [1,2,3]) s.selects[i].visible=false;
    verifyChoices(s,{phone,runtime:'claude'});
    const missing=structuredClone(s);missing.claudeModel='';
    assert.throws(()=>verifyChoices(missing,{phone,runtime:'claude'}));
    s.selects[4].options=[];
    assert.throws(()=>verifyChoices(s,{phone,runtime:'claude'}));
  });
}
