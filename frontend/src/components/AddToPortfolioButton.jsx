import { useApp } from "../App.jsx";

export default function AddToPortfolioButton({ market, ticker, company = "", className = "" }) {
  const { inPortfolio, addToPortfolio, removeFromPortfolio } = useApp();
  const present = market && ticker && inPortfolio(market, ticker);
  if (present) {
    return (
      <button
        className={`add-portfolio on ${className}`}
        title="Remove from portfolio"
        onClick={(e) => {
          e.stopPropagation();
          removeFromPortfolio(market, ticker);
        }}
      >
        ✓ IN PORTFOLIO
      </button>
    );
  }
  return (
    <button
      className={`add-portfolio ${className}`}
      title="Add to portfolio"
      onClick={(e) => {
        e.stopPropagation();
        addToPortfolio(market, ticker, company);
      }}
    >
      + ADD TO PORTFOLIO
    </button>
  );
}