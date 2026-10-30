import React from 'react';
import {ErrorBox} from './Common.jsx';

export default class ErrorBoundary extends React.Component {
  constructor(props){super(props);this.state={error:null};}
  static getDerivedStateFromError(error){return {error};}
  componentDidCatch(error,info){console.error('Dashboard render failed',error,info);}
  render(){
    if(this.state.error) return <main className="main-shell"><ErrorBox error={`This view failed to render: ${this.state.error.message}. Reload after checking the backend.`}/><button className="primary" onClick={()=>window.location.reload()}>Reload dashboard</button></main>;
    return this.props.children;
  }
}
