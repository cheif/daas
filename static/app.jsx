class Container extends React.Component {
  constructor(props) {
    super(props)
    this.state = props.data
  }
  envChange(e) {
    this.setState({
      env: e.target.value.split('\n')
    })
  }
  updateEnv() {
    fetch(`/config/${this.state.alias}/`, {
      method: 'put',
      credentials: 'include',
      body: JSON.stringify({
        env: this.state.env
      })
    }).then(this.props.update)
  }
  render() {
    const {alias, env, state} = this.state
    return (
      <div className='container'>
        <div className='container__name'>
          {alias}:{state}
        </div>
        <div className='container__env'>
          <textarea onChange={this.envChange.bind(this)} defaultValue={env.join('\n')} />
        </div>
        <button disabled={this.props.data == this.state} onClick={this.updateEnv.bind(this)}>Save</button>
      </div>
    )
  }
}

class ContainerList extends React.Component {
  constructor(props) {
    super(props)
    this.state = {
      containers: []
    }
  }
  componentWillMount() {
    this.refresh()
  }
  refresh() {
    fetch('/config', {credentials: 'include'}).then(r => r.json()).then(containers =>
      this.setState({containers: containers}))
  }
  render() {
    return (
      <div>
        <h2>Running containers:</h2>
      {this.state.containers.map(c => <Container key={c.alias} data={c} update={this.refresh.bind(this)} />)}
      </div>
    )
  }
}

ReactDOM.render(
  <ContainerList />,
  document.getElementById('content')
)
