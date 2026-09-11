"""
Export Base_PowerBI.xlsx sheets to JSON files for static web dashboard.
Pre-aggregates daily data for smaller file sizes.
"""
import pandas as pd
import json
import os
import numpy as np

def df_to_json(df):
    """Convert DataFrame to list of dicts, handling NaN."""
    records = df.to_dict(orient='records')
    cleaned = []
    for record in records:
        cleaned_record = {}
        for k, v in record.items():
            if isinstance(v, (pd.Timestamp, np.datetime64)):
                cleaned_record[k] = str(v)[:10]
            elif isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
                cleaned_record[k] = None
            elif isinstance(v, (np.integer,)):
                cleaned_record[k] = int(v)
            elif isinstance(v, (np.floating,)):
                cleaned_record[k] = round(float(v), 2)
            else:
                cleaned_record[k] = v
        cleaned.append(cleaned_record)
    return cleaned

def export_daily_aggregated(file_path, out_dir):
    """Pre-aggregate daily data for the heatmap and day-of-week charts."""
    df = pd.read_excel(file_path, sheet_name='fato_vendas_diarias')
    df['Data'] = pd.to_datetime(df['Data'])

    # Daily totals (for heatmap)
    daily = df.groupby('Data').agg(
        Vlr_Venda=('Vlr_Venda', 'sum'),
        Qtde_Documentos=('Qtde_Documentos', 'sum'),
    ).reset_index()
    daily['Data'] = daily['Data'].dt.strftime('%Y-%m-%d')
    daily['Dia_Semana'] = pd.to_datetime(daily['Data']).dt.day_name()
    daily['Semana'] = pd.to_datetime(daily['Data']).dt.isocalendar().week.astype(int)

    data = df_to_json(daily)
    with open(os.path.join(out_dir, 'vendas_diarias.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"  vendas_diarias -> {len(data)} daily records")

def main():
    file_path = "Base_PowerBI.xlsx"
    out_dir = "public/data"
    os.makedirs(out_dir, exist_ok=True)

    sheets = {
        'vendas_mensais': 'fato_vendas_mensais',
        'produtos': 'dim_produtos',
        'calendario': 'dim_calendario',
        'yoy': 'comparativo_yoy',
        'erosao': 'alertas_erosao_margem',
    }

    for key, sheet_name in sheets.items():
        print(f"Exporting {sheet_name} -> {key}.json ...")
        df = pd.read_excel(file_path, sheet_name=sheet_name)
        data = df_to_json(df)
        with open(os.path.join(out_dir, f"{key}.json"), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        print(f"  -> {len(data)} records")

    print("Exporting vendas_diarias (aggregated) ...")
    export_daily_aggregated(file_path, out_dir)

    print("Done!")

if __name__ == "__main__":
    main()
