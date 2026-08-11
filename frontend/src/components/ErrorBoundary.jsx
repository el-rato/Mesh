import { Component } from "react";

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("ErrorBoundary caught:", error, info);
  }

  retry = () => {
    this.setState({ error: null });
  };

  render() {
    if (this.state.error) {
      const msg =
        this.state.error && this.state.error.message
          ? this.state.error.message
          : String(this.state.error);
      return (
        <div className="error">
          <div style={{ marginBottom: 12 }}>ERROR: {msg}</div>
          <button className="primary" onClick={this.retry}>
            ⟳ RETRY
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
