import React from 'react';
import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import Report from './components/Report.jsx';
import fixture from '../tests/report.fixture.json';
const render = report => renderToStaticMarkup(React.createElement(Report,{report}));
describe('Report derivative context',()=>{
 it('labels open-interest change as context without a score vote',()=>{
  const report=structuredClone(fixture);
  report.assessment.derivative_context={available:true,open_interest_change_percent:30,interpretation:'Price and open interest rose together.'};
  const html=render(report);
  expect(html).toContain('OI ~24h: 30% (context only, not a score vote).');
  expect(html).toContain('Price and open interest rose together.');
 });
 it('renders saved reports that predate derivative context',()=>{
  const report=structuredClone(fixture);
  delete report.assessment.derivative_context;
  expect(render(report)).not.toContain('OI ~24h:');
 });
 it('replaces an expired candidate assessment with an explicit NO TRADE message',()=>{
  const report=structuredClone(fixture);
  report.generated_at=new Date().toISOString();report.valid_until_ms=Date.now()-1;
  report.assessment.classification='LONG candidate';report.assessment.final_assessment='LONG candidate: experimental evidence.';
  const html=render(report);
  expect(html).toContain('NO TRADE: required source data expired.');
  expect(html).not.toContain('LONG candidate: experimental evidence.');
  expect(html).not.toContain('Open hypothetical planner');
 });
});
