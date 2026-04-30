import json

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


def plot_weekday_analysis() -> None:
    try:
        # Load the data
        with open("zhihu_income_report.json", encoding="utf-8") as f:
            data = json.load(f)

        df = pd.DataFrame(data["details"])
        df["date"] = pd.to_datetime(df["date"])

        # Extract day of the week (0=Monday, 6=Sunday)
        df["day_of_week"] = df["date"].dt.day_name()

        # Reorder days to start from Monday
        days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

        plt.figure(figsize=(12, 7))

        # Create the Box Plot
        # color='#0084ff' matches the Zhihu brand color
        sns.boxplot(
            x="day_of_week",
            y="income_yuan",
            data=df,
            order=days_order,
            palette="Blues",
            showmeans=True,
            meanprops={"marker": "o", "markerfacecolor": "white", "markeredgecolor": "black", "markersize": "8"},
        )

        # Add a Swarm Plot on top to see individual data points (optional but helpful)
        sns.swarmplot(x="day_of_week", y="income_yuan", data=df, order=days_order, color=".25", size=4, alpha=0.6)

        # Labels and Title in English
        plt.title("Zhihu Income Distribution by Day of the Week", fontsize=16, pad=20)
        plt.xlabel("Day of the Week", fontsize=12)
        plt.ylabel("Income (CNY / Yuan)", fontsize=12)

        # Statistical summary for the printout
        stats = df.groupby("day_of_week")["income_yuan"].agg(["mean", "median", "std"]).reindex(days_order)
        print("\n--- Weekly Statistical Summary ---")
        print(stats)

        best_day = stats["mean"].idxmax()
        print(f"\n💡 Insight: Your most profitable day on average is {best_day}.")

        plt.tight_layout()
        plt.savefig("weekday_income_analysis.png", dpi=500, bbox_inches="tight")
        print("\n✅ Box plot saved as: weekday_income_analysis.png")
        plt.show()

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    plot_weekday_analysis()
