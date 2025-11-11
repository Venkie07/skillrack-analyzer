-- Run this in your Supabase SQL editor
CREATE TABLE skillrack_profiles (
    id TEXT PRIMARY KEY,
    name TEXT,
    college TEXT,
    points INTEGER DEFAULT 0,
    last_fetched TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    dc INTEGER DEFAULT 0,
    dt INTEGER DEFAULT 0,
    profile_url TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create index for better performance
CREATE INDEX idx_skillrack_profiles_points ON skillrack_profiles(points DESC);
CREATE INDEX idx_skillrack_profiles_last_fetched ON skillrack_profiles(last_fetched DESC);

-- Update updated_at trigger
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_skillrack_profiles_updated_at 
    BEFORE UPDATE ON skillrack_profiles 
    FOR EACH ROW 
    EXECUTE FUNCTION update_updated_at_column();